"""The Loom - Where Claude becomes Alpha.

A reverse proxy that weaves together the threads of identity:
memories, context, metadata, and the conversation itself.
"""

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import httpx
import json
import logging

from pondside.telemetry import init, get_tracer

# Initialize telemetry
init("loom")
logger = logging.getLogger(__name__)
tracer = get_tracer()

app = FastAPI(title="The Loom", description="Where Claude becomes Alpha")

# Persistent client for connection pooling
client = httpx.AsyncClient(
    base_url="https://api.anthropic.com",
    timeout=httpx.Timeout(300.0, connect=10.0),  # Long timeout for LLM responses
)

# The canary that marks metadata blocks
CANARY = "EAVESDROP_METADATA_BLOCK_UlVCQkVSRFVDSw"


def extract_metadata(body: dict) -> dict | None:
    """Find and remove the metadata block from the request.

    Returns the extracted metadata, or None if not found.
    Modifies body in place to remove the canary block.
    """
    messages = body.get("messages", [])

    # Search backwards through messages
    for msg_idx in range(len(messages) - 1, -1, -1):
        msg = messages[msg_idx]
        if msg.get("role") != "user":
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        # Search content blocks for the canary
        for block_idx, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "text":
                continue

            text = block.get("text", "")
            if CANARY not in text:
                continue

            # Must be in a system-reminder tag (not just mentioned in conversation)
            if "<system-reminder>" not in text:
                continue

            # Found it! Pop the block
            content.pop(block_idx)
            logger.info(f"Removed canary block {block_idx} from message {msg_idx}")

            # Extract JSON: everything between first { and last }
            try:
                start = text.index("{")
                end = text.rindex("}") + 1
                json_str = text[start:end]
                metadata = json.loads(json_str)
                logger.info(f"Extracted metadata: session={metadata.get('session_id', '?')}")
                return metadata
            except (ValueError, json.JSONDecodeError) as e:
                logger.error(f"Failed to parse metadata JSON: {e}")
                return None

    return None


async def stream_response(response: httpx.Response):
    """Yield chunks from the upstream response."""
    async for chunk in response.aiter_bytes():
        yield chunk


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(request: Request, path: str):
    """Proxy all requests to Anthropic, weaving in Alpha's threads."""

    with tracer.start_as_current_span(f"loom.{request.method.lower()}") as span:
        # Get request body
        body_bytes = await request.body()

        # For POST to /v1/messages, do our magic
        metadata = None
        if request.method == "POST" and "messages" in path:
            try:
                body = json.loads(body_bytes)

                # Extract and remove metadata block
                metadata = extract_metadata(body)
                if metadata:
                    span.set_attribute("loom.session_id", metadata.get("session_id", ""))
                    span.set_attribute("loom.source", metadata.get("source", ""))
                    span.set_attribute("loom.is_alpha", True)
                else:
                    span.set_attribute("loom.is_alpha", False)

                # TODO: Inject memories from Cortex
                # TODO: Compose system prompt from Redis

                # Re-encode the modified body
                body_bytes = json.dumps(body).encode()

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse request body: {e}")

        # Forward headers (filter out host)
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length")
        }

        # Make the upstream request
        upstream_response = await client.request(
            method=request.method,
            url=f"/{path}",
            headers=headers,
            content=body_bytes,
            params=request.query_params,
        )

        # Log quota headers
        if "anthropic-ratelimit-unified-5h-utilization" in upstream_response.headers:
            util = upstream_response.headers["anthropic-ratelimit-unified-5h-utilization"]
            logger.info(f"Quota: 5h={util}")

        # Check if streaming
        content_type = upstream_response.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            # Stream SSE response
            return StreamingResponse(
                stream_response(upstream_response),
                status_code=upstream_response.status_code,
                headers=dict(upstream_response.headers),
                media_type="text/event-stream",
            )
        else:
            # Return full response
            return Response(
                content=upstream_response.content,
                status_code=upstream_response.status_code,
                headers=dict(upstream_response.headers),
            )


@app.on_event("shutdown")
async def shutdown():
    await client.aclose()
