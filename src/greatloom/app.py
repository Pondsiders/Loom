"""The Great Loom - FastAPI application.

Where Claude becomes whoever you need.
"""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import logfire
from opentelemetry.propagate import extract

from .router import init_patterns, get_pattern_from_request
from . import proxy

# Suppress harmless OTel context warnings before they're configured
logging.getLogger("opentelemetry.context").setLevel(logging.CRITICAL)

# Initialize Logfire
# Scrubbing disabled - too aggressive (redacts "session", "auth", etc.)
# Our logs are authenticated with 30-day retention; acceptable risk for debugging visibility
logfire.configure(distributed_tracing=True, scrubbing=False)
logfire.instrument_httpx()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    logfire.info("The Great Loom is starting up...")
    init_patterns()
    logfire.info("The Great Loom is ready.")
    yield
    logfire.info("The Great Loom is shutting down...")
    await proxy.close()


app = FastAPI(
    title="The Great Loom",
    description="Where Claude becomes whoever you need.",
    lifespan=lifespan,
)

# Instrument FastAPI
logfire.instrument_fastapi(app)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "greatloom"}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def handle_request(request: Request, path: str):
    """Route requests through the appropriate pattern."""

    body_bytes = await request.body()
    headers = dict(request.headers)

    # Extract distributed tracing context from incoming request
    traceparent = headers.get("traceparent")
    parent_ctx = None
    if traceparent:
        parent_ctx = extract({"traceparent": traceparent})

    # Parse body for pattern selection and transformation
    body = None
    is_messages_endpoint = request.method == "POST" and "messages" in path

    if is_messages_endpoint and body_bytes:
        try:
            body = json.loads(body_bytes)
        except json.JSONDecodeError:
            logfire.warning("Failed to parse request body as JSON")

    # Select pattern
    pattern = get_pattern_from_request(headers, body or {})
    pattern_name = type(pattern).__name__

    # Extract model and session for span attributes
    model = body.get("model", "unknown") if body else "unknown"
    session_id = headers.get("x-session-id", "")

    # Attach parent context if available
    if parent_ctx:
        ctx_manager = logfire.attach_context(parent_ctx)
        ctx_manager.__enter__()
    else:
        ctx_manager = None

    # Create span for this request
    span_name = f"loom: {request.method} /{path}"
    if is_messages_endpoint and model != "unknown":
        span_name = f"loom: POST /v1/messages ({model}, {pattern_name.lower().replace('pattern', '')})"

    span = logfire.span(
        span_name,
        pattern=pattern_name,
        model=model,
        session_id=session_id[:8] if session_id else None,
        endpoint=f"/{path}",
    )
    span.__enter__()

    logfire.info(
        f"{pattern_name} request ({model}): {len(body.get('messages', []))} messages" if body else f"{pattern_name} request",
        pattern=pattern_name,
        model=model,
        session=session_id[:8] if session_id else "none",
        message_count=len(body.get("messages", [])) if body else 0,
    )

    try:
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

        span.set_attribute("http.status_code", status_code)

        if "text/event-stream" in content_type:
            # Streaming response - pass through, call pattern.response with None body
            # Keep span open through streaming
            captured_span = span
            captured_ctx_manager = ctx_manager

            async def stream_with_transform():
                try:
                    async for chunk in upstream_response.aiter_bytes():
                        yield chunk
                    # After streaming, call response hook (body=None for streams)
                    await pattern.response(response_headers, None)
                finally:
                    try:
                        captured_span.__exit__(None, None, None)
                    except ValueError:
                        pass  # Cross-context detach, harmless
                    if captured_ctx_manager:
                        try:
                            captured_ctx_manager.__exit__(None, None, None)
                        except ValueError:
                            pass  # Cross-context detach, harmless

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

            span.__exit__(None, None, None)
            if ctx_manager:
                ctx_manager.__exit__(None, None, None)

            return Response(
                content=response_content,
                status_code=status_code,
                headers=response_headers,
            )

    except Exception as e:
        span.record_exception(e)
        span.set_level("error")
        span.__exit__(None, None, None)
        if ctx_manager:
            ctx_manager.__exit__(None, None, None)
        logfire.error("Loom error", error=str(e))
        raise
