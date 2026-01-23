"""The Great Loom - FastAPI application.

Where Claude becomes whoever you need.
"""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from .router import init_patterns, get_pattern_from_request
from . import proxy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    logger.info("The Great Loom is starting up...")
    init_patterns()
    logger.info("The Great Loom is ready.")
    yield
    logger.info("The Great Loom is shutting down...")
    await proxy.close()


app = FastAPI(
    title="The Great Loom",
    description="Where Claude becomes whoever you need.",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "greatloom"}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def handle_request(request: Request, path: str):
    """Route requests through the appropriate pattern."""

    body_bytes = await request.body()
    headers = dict(request.headers)

    # Parse body for pattern selection and transformation
    body = None
    is_messages_endpoint = request.method == "POST" and "messages" in path

    if is_messages_endpoint and body_bytes:
        try:
            body = json.loads(body_bytes)
        except json.JSONDecodeError:
            logger.warning("Failed to parse request body as JSON")

    # Select pattern
    pattern = get_pattern_from_request(headers, body or {})
    logger.info(f"Request to /{path} using pattern: {type(pattern).__name__}")

    # Transform request
    if body is not None:
        headers, body = await pattern.request(headers, body)
        body_bytes = json.dumps(body).encode()

    # Forward to upstream
    forward_headers = proxy.filter_request_headers(headers)

    upstream_response = await proxy.forward_request(
        method=request.method,
        path=path,
        headers=forward_headers,
        content=body_bytes,
        params=dict(request.query_params),
    )

    # Prepare response
    content_type = upstream_response.headers.get("content-type", "")
    response_headers = proxy.filter_response_headers(dict(upstream_response.headers))
    status_code = upstream_response.status_code

    if "text/event-stream" in content_type:
        # Streaming response - pass through, call pattern.response with None body
        async def stream_with_transform():
            async for chunk in upstream_response.aiter_bytes():
                yield chunk
            # After streaming, call response hook (body=None for streams)
            await pattern.response(response_headers, None)

        return StreamingResponse(
            stream_with_transform(),
            status_code=status_code,
            headers=response_headers,
            media_type="text/event-stream",
        )
    else:
        # Non-streaming response - transform body
        response_content = upstream_response.content

        try:
            response_body = json.loads(response_content)
            response_headers, response_body = await pattern.response(
                response_headers, response_body
            )
            response_content = json.dumps(response_body).encode()
        except json.JSONDecodeError:
            # Not JSON, just pass through
            await pattern.response(response_headers, None)

        return Response(
            content=response_content,
            status_code=status_code,
            headers=response_headers,
        )
