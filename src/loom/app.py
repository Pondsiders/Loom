"""The Loom - FastAPI application."""

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from opentelemetry import trace as otel_trace
from opentelemetry.context import attach, detach as otel_detach, set_value
from opentelemetry.trace import set_span_in_context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


def detach(token):
    """Detach context, silently handling cross-context token errors.

    In async streaming handlers, tokens created in one coroutine context
    may be detached in another. This is expected and harmless—the context
    cleanup happens regardless. We just suppress the noisy warning.
    """
    try:
        otel_detach(token)
    except ValueError:
        # "Token was created in a different Context"
        # This is an expection, not an exception. 🦆
        pass


# Suppress the "Failed to detach context" warnings from OTel
# These happen in async streaming handlers and are harmless expections 🦆
logging.getLogger("opentelemetry.context").setLevel(logging.CRITICAL)

from pondside.telemetry import init, get_tracer

from .metadata import extract_metadata
from .compact import rewrite_auto_compact
# LLM span creation moved to Argonath - import kept for reference
# from .llm_spans import create_llm_span
from .traces import TraceManager
from .watcher import ensure_watcher
from .quota import log_quota
from .intro import (
    get_memorables,
    format_memorables_block,
    inject_memorables,
)
from .prompt import init_eternal_prompt, inject_system_prompt
from . import proxy

# Initialize telemetry
init("loom")

# Claude data directory - override in Docker where Path.home() != host home
CLAUDE_DATA_DIR = Path(os.getenv("CLAUDE_DATA_DIR", str(Path.home() / ".claude")))
logger = logging.getLogger(__name__)
tracer = get_tracer()
trace_manager = TraceManager(tracer)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    # Initialize eternal prompt at startup (fetches from GitHub or disk)
    init_eternal_prompt()
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

    # Track state for LLM span
    is_messages_endpoint = request.method == "POST" and "messages" in path
    request_body = None
    metadata = None
    is_alpha = False
    session_id = None
    trace_id = None
    traceparent = None
    prompt = None
    parent_context = None
    model_short = "n/a"
    model_name = "unknown"

    # Phase 1: Parse body and extract metadata WITHOUT LOGGING
    # We need model name for span naming and traceparent for context propagation
    # before we create the root span
    if is_messages_endpoint:
        try:
            request_body = json.loads(body_bytes)
            model_name = request_body.get("model", "unknown")

            # Shorten model name for readability
            if "opus" in model_name.lower():
                model_short = "opus"
            elif "sonnet" in model_name.lower():
                model_short = "sonnet"
            elif "haiku" in model_name.lower():
                model_short = "haiku"
            else:
                model_short = model_name.split("-")[0] if "-" in model_name else model_name

            # Extract metadata from headers FIRST (via Deliverator), then fall back to body
            # Headers contain the CURRENT turn's traceparent; body may have stale ones
            # from previous turns in the conversation history
            header_session = request.headers.get("x-session-id")
            header_traceparent = request.headers.get("traceparent")

            if header_session:
                # Headers found (via Deliverator) - use these, they're fresh
                is_alpha = True
                session_id = header_session
                traceparent = header_traceparent
                # trace_id can be extracted from traceparent: 00-{trace_id}-{span_id}-{flags}
                if header_traceparent:
                    parts = header_traceparent.split("-")
                    if len(parts) >= 2:
                        trace_id = parts[1]
                # Still extract body metadata for other fields (prompt, machine info)
                # but don't use its traceparent
                metadata = extract_metadata(request_body, log=False)
                if metadata:
                    prompt = metadata.get("prompt", "")
            else:
                # No headers - check body (direct to Loom, not via Deliverator)
                metadata = extract_metadata(request_body, log=False)
                if metadata:
                    is_alpha = True
                    session_id = metadata.get("session_id")
                    trace_id = metadata.get("trace_id")
                    traceparent = metadata.get("traceparent")
                    prompt = metadata.get("prompt", "")

            # Get parent context from traceparent (from body or headers)
            if traceparent:
                carrier = {"traceparent": traceparent}
                parent_context = TraceContextTextMapPropagator().extract(carrier=carrier)

        except json.JSONDecodeError:
            model_short = "error"

    # Phase 2: Create root span with good name, using client context if available
    # For Alpha requests: parent to the client's trace (UserPromptSubmit hook)
    # For SDK requests: create a new root trace with model name for filtering
    if is_alpha:
        span_name = f"loom: POST /v1/messages ({model_short}, alpha)"
    elif is_messages_endpoint:
        span_name = f"loom: POST /v1/messages ({model_short})"
    else:
        span_name = f"loom: {request.method} /{path}"

    root_span = tracer.start_span(span_name, context=parent_context)

    # Make this span the current span so all child work is parented to it
    span_context = set_span_in_context(root_span)
    token = attach(span_context)

    try:
        # Phase 3: Set attributes and log (now safely parented)
        root_span.set_attribute("model", model_name)
        root_span.set_attribute("is_alpha", is_alpha)
        if session_id:
            root_span.set_attribute("session_id", session_id[:8])
        if trace_id:
            root_span.set_attribute("client_trace_id", trace_id[:8])

        if is_messages_endpoint and request_body:
            msg_count = len(request_body.get('messages', []))
            if metadata:
                logger.info(f"Alpha request ({model_short}): {msg_count} messages, session={session_id[:8] if session_id else 'none'}")
            else:
                logger.info(f"SDK request ({model_short}): {msg_count} messages")

        # Continue processing the request
        if is_messages_endpoint and request_body:
            # Get or create parent trace for this turn (for accumulated state tracking)
            if trace_id and session_id:
                active_trace, parent_context = trace_manager.get_or_create_trace(
                    trace_id=trace_id,
                    session_id=session_id,
                    prompt=prompt,
                    is_alpha=is_alpha,
                )

            # Start/refresh transcript watcher
            # Build path locally rather than trusting metadata (which has source machine's absolute path)
            if session_id:
                transcript_path = CLAUDE_DATA_DIR / "projects" / "-Pondside" / f"{session_id}.jsonl"
                logger.info(f"Watcher check: session={session_id[:8]}, path={transcript_path}, exists={transcript_path.exists()}")
                if transcript_path.exists():
                    await ensure_watcher(session_id, str(transcript_path))

            # Rewrite auto-compact prompts if detected
            request_body = rewrite_auto_compact(request_body, is_alpha=is_alpha)

            # Intro buffer clearing is handled via pubsub (cortex:stored:*)
            # No canary checking needed here—Intro listens directly

            # Inject Intro's memorables
            if is_alpha and session_id:
                memorables = await get_memorables(session_id)
                if memorables:
                    memorables_block = format_memorables_block(memorables)
                    request_body = inject_memorables(request_body, session_id, memorables_block)

            # Inject assembled system prompt (eternal + past/present/future from Redis)
            if is_alpha:
                machine_name = metadata.get("machine", {}).get("fqdn", "").split(".")[0] if metadata else None
                request_body = inject_system_prompt(request_body, machine_name=machine_name)

            # Re-encode the modified body
            body_bytes = json.dumps(request_body).encode()

        # Forward to Anthropic (or Argonath)
        headers = proxy.filter_request_headers(dict(request.headers))

        # Inject current span context into headers for trace propagation
        # This updates traceparent to point to the Loom's span, so downstream
        # services (like Argonath) create child spans under us, not siblings
        TraceContextTextMapPropagator().inject(headers)

        # Also forward session_id for correlation (Argonath uses this)
        if session_id:
            headers["x-session-id"] = session_id

        upstream_response = await proxy.forward_request(
            method=request.method,
            path=path,
            headers=headers,
            content=body_bytes,
            params=dict(request.query_params),
        )

        # Log quota headers to Redis (for dashboard)
        log_quota(dict(upstream_response.headers))

        # Prepare response
        content_type = upstream_response.headers.get("content-type", "")
        response_headers = proxy.filter_response_headers(dict(upstream_response.headers))
        status_code = upstream_response.status_code

        if "text/event-stream" in content_type:
            # Streaming response - capture chunks for LLM span
            chunks: list[bytes] = []

            # Capture the token so we can detach in the generator
            captured_token = token

            async def stream_with_span():
                # Re-attach the context for the streaming generator
                # (generators run in a different context than the handler)
                stream_token = attach(span_context)
                try:
                    async for chunk in _stream_and_capture(upstream_response, chunks):
                        yield chunk

                    # After streaming completes, update trace
                    # NOTE: LLM span creation moved to Argonath (observability proxy)
                    if is_messages_endpoint and request_body:
                        end_time_ns = time.time_ns()
                        response_body = _parse_sse_response(chunks)

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
                finally:
                    # End root span and detach context when streaming completes
                    root_span.end()
                    detach(stream_token)
                    detach(captured_token)

            # Return streaming response - the generator will handle span cleanup
            # Don't detach here; the generator will do it
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

            # NOTE: LLM span creation moved to Argonath (observability proxy)
            # Parse response for trace management
            if is_messages_endpoint and request_body:
                try:
                    response_body = json.loads(response_content)
                except json.JSONDecodeError:
                    response_body = None

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

            # End span and detach for non-streaming response
            root_span.end()
            detach(token)

            return Response(
                content=response_content,
                status_code=status_code,
                headers=response_headers,
            )

    except Exception as e:
        # On error, record it on the span and clean up
        root_span.record_exception(e)
        root_span.end()
        detach(token)
        raise
