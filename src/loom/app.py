"""The Loom - FastAPI application."""

import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from pondside.telemetry import init, get_tracer

from .metadata import extract_metadata
from .compact import rewrite_auto_compact
from .llm_spans import create_llm_span
from .traces import TraceManager
from . import proxy

# Initialize telemetry
init("loom")
logger = logging.getLogger(__name__)
tracer = get_tracer()
trace_manager = TraceManager(tracer)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    yield
    await proxy.close()


app = FastAPI(
    title="The Loom",
    description="Where Claude becomes Alpha",
    lifespan=lifespan,
)


def _parse_sse_response(chunks: list[bytes]) -> dict | None:
    """Parse SSE chunks to extract usage, content, and tool_use from streaming response.

    Anthropic streaming sends:
    - message_start: contains input_tokens in usage
    - content_block_start: announces a new content block (text or tool_use)
    - content_block_delta: contains text fragments or tool input fragments
    - message_delta: contains output_tokens in usage
    - message_stop: end marker

    Returns a dict with:
    - usage: {input_tokens, output_tokens}
    - content: list of content blocks
    - has_tool_use: bool indicating if response contains tool calls
    """
    full_text = b"".join(chunks).decode("utf-8", errors="replace")

    input_tokens = 0
    output_tokens = 0
    text_parts: list[str] = []
    has_tool_use = False
    tool_uses: list[dict] = []
    current_tool: dict | None = None

    for line in full_text.split("\n"):
        if not line.startswith("data: "):
            continue
        try:
            data = json.loads(line[6:])
            event_type = data.get("type")

            if event_type == "message_start":
                # message_start has the initial usage with input_tokens
                message = data.get("message", {})
                usage = message.get("usage", {})
                input_tokens = usage.get("input_tokens", 0)
                logger.debug(f"SSE message_start: input_tokens={input_tokens}")

            elif event_type == "content_block_start":
                # content_block_start announces what kind of block is coming
                content_block = data.get("content_block", {})
                if content_block.get("type") == "tool_use":
                    has_tool_use = True
                    current_tool = {
                        "type": "tool_use",
                        "id": content_block.get("id", ""),
                        "name": content_block.get("name", ""),
                        "input_json": "",
                    }

            elif event_type == "content_block_delta":
                delta = data.get("delta", {})
                if delta.get("type") == "text_delta":
                    text_parts.append(delta.get("text", ""))
                elif delta.get("type") == "input_json_delta" and current_tool:
                    current_tool["input_json"] += delta.get("partial_json", "")

            elif event_type == "content_block_stop":
                # Finalize current tool if any
                if current_tool:
                    try:
                        current_tool["input"] = json.loads(current_tool["input_json"]) if current_tool["input_json"] else {}
                    except json.JSONDecodeError:
                        current_tool["input"] = {}
                    del current_tool["input_json"]
                    tool_uses.append(current_tool)
                    current_tool = None

            elif event_type == "message_delta":
                # message_delta has output_tokens
                usage = data.get("usage", {})
                output_tokens = usage.get("output_tokens", 0)
                logger.debug(f"SSE message_delta: output_tokens={output_tokens}")

        except json.JSONDecodeError:
            continue

    response_text = "".join(text_parts)
    logger.info(f"SSE parsed: input={input_tokens}, output={output_tokens}, text_len={len(response_text)}, has_tool_use={has_tool_use}")

    if input_tokens or output_tokens or response_text or has_tool_use:
        content = []
        if response_text:
            content.append({"type": "text", "text": response_text})
        content.extend(tool_uses)

        return {
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            "content": content,
            "has_tool_use": has_tool_use,
        }

    return None


async def _stream_and_capture(upstream_response, chunks_list: list):
    """Stream response while capturing chunks for later analysis."""
    async for chunk in upstream_response.aiter_bytes():
        chunks_list.append(chunk)
        yield chunk


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def handle_request(request: Request, path: str):
    """Proxy all requests to Anthropic, weaving in Alpha's threads."""

    start_time_ns = time.time_ns()
    body_bytes = await request.body()

    logger.info(f"Request: {request.method} /{path}")

    # Track state for LLM span
    is_messages_endpoint = request.method == "POST" and "messages" in path
    request_body = None
    metadata = None
    is_alpha = False
    session_id = None
    trace_id = None
    prompt = None
    parent_context = None

    # For POST to /v1/messages, do our magic
    if is_messages_endpoint:
        try:
            request_body = json.loads(body_bytes)

            logger.info(f"Processing /v1/messages with {len(request_body.get('messages', []))} messages")
            metadata = extract_metadata(request_body)

            if metadata:
                is_alpha = True
                session_id = metadata.get("session_id")
                trace_id = metadata.get("trace_id")
                prompt = metadata.get("prompt", "")
                logger.info(f"Alpha request: session={session_id}, trace={trace_id}")

                # Get or create parent trace for this turn
                if trace_id and session_id:
                    active_trace, parent_context = trace_manager.get_or_create_trace(
                        trace_id=trace_id,
                        session_id=session_id,
                        prompt=prompt,
                        is_alpha=is_alpha,
                    )

            # Rewrite auto-compact prompts if detected
            request_body = rewrite_auto_compact(request_body, is_alpha=is_alpha)

            # TODO: Compose system prompt from Redis
            # TODO: Inject memories from Cortex

            # Re-encode the modified body
            body_bytes = json.dumps(request_body).encode()

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse request body: {e}")

    # Forward to Anthropic
    headers = proxy.filter_request_headers(dict(request.headers))

    upstream_response = await proxy.forward_request(
        method=request.method,
        path=path,
        headers=headers,
        content=body_bytes,
        params=dict(request.query_params),
    )

    # Log quota headers
    if "anthropic-ratelimit-unified-5h-utilization" in upstream_response.headers:
        util = upstream_response.headers["anthropic-ratelimit-unified-5h-utilization"]
        logger.info(f"Quota: 5h={util}")

    # Prepare response
    content_type = upstream_response.headers.get("content-type", "")
    response_headers = proxy.filter_response_headers(dict(upstream_response.headers))
    status_code = upstream_response.status_code

    if "text/event-stream" in content_type:
        # Streaming response - capture chunks for LLM span
        chunks: list[bytes] = []

        async def stream_with_span():
            async for chunk in _stream_and_capture(upstream_response, chunks):
                yield chunk

            # After streaming completes, create LLM span and update trace
            if is_messages_endpoint and request_body:
                end_time_ns = time.time_ns()
                response_body = _parse_sse_response(chunks)

                # Create child span under parent trace
                create_llm_span(
                    tracer=tracer,
                    request_body=request_body,
                    response_body=response_body,
                    status_code=status_code,
                    start_time_ns=start_time_ns,
                    end_time_ns=end_time_ns,
                    session_id=session_id,
                    is_alpha=is_alpha,
                    parent_context=parent_context,
                )

                # Update trace with this span's results
                if trace_id and response_body:
                    text_output = ""
                    for block in response_body.get("content", []):
                        if block.get("type") == "text":
                            text_output = block.get("text", "")
                            break

                    usage = response_body.get("usage", {})
                    trace_manager.add_span_result(
                        trace_id=trace_id,
                        text_output=text_output,
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                    )

                    # If no tool_use in response, this turn is complete
                    if not response_body.get("has_tool_use", False):
                        trace_manager.finalize_trace(trace_id)

        return StreamingResponse(
            stream_with_span(),
            status_code=status_code,
            headers=response_headers,
            media_type="text/event-stream",
        )
    else:
        # Non-streaming response
        response_content = upstream_response.content
        end_time_ns = time.time_ns()

        # Create LLM span for messages endpoint
        if is_messages_endpoint and request_body:
            try:
                response_body = json.loads(response_content)
            except json.JSONDecodeError:
                response_body = None

            create_llm_span(
                tracer=tracer,
                request_body=request_body,
                response_body=response_body,
                status_code=status_code,
                start_time_ns=start_time_ns,
                end_time_ns=end_time_ns,
                session_id=session_id,
                is_alpha=is_alpha,
                parent_context=parent_context,
            )

            # Update trace with this span's results
            if trace_id and response_body:
                text_output = ""
                content_blocks = response_body.get("content", [])
                has_tool_use = False

                for block in content_blocks:
                    if block.get("type") == "text":
                        text_output = block.get("text", "")
                    elif block.get("type") == "tool_use":
                        has_tool_use = True

                usage = response_body.get("usage", {})
                trace_manager.add_span_result(
                    trace_id=trace_id,
                    text_output=text_output,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                )

                # If no tool_use in response, this turn is complete
                if not has_tool_use:
                    trace_manager.finalize_trace(trace_id)

        return Response(
            content=response_content,
            status_code=status_code,
            headers=response_headers,
        )
