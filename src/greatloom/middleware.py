"""Deliverator middleware - metadata extraction and header promotion.

This middleware extracts DELIVERATOR_METADATA from request bodies and promotes
traceparent, session_id, and pattern to HTTP headers. It runs BEFORE Logfire's
auto-instrumentation, so the auto-created spans see the traceparent header and
create proper nested traces.

The name lives on. The extra network hop doesn't.
"""

import json
import logging

import logfire
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

# The canaries that mark metadata blocks
DELIVERATOR_CANARY = "DELIVERATOR_METADATA_UlVCQkVSRFVDSw"
LOOM_CANARY = "LOOM_METADATA_UlVCQkVSRFVDSw"


def extract_metadata_from_body(body: dict) -> dict | None:
    """Find canary blocks, extract metadata from the LAST one found.

    Looks for DELIVERATOR_METADATA first (new path), then LOOM_METADATA (legacy).
    Returns metadata dict or None if not found.
    """
    messages = body.get("messages", [])

    found_blocks = []  # List of (metadata, canary_type)

    for msg in messages:
        if msg.get("role") != "user":
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue

            text = block.get("text", "")

            # Debug: log text blocks that might contain canaries
            if "DELIVERATOR" in text or "LOOM_METADATA" in text:
                logfire.debug(
                    "Found potential canary text",
                    text_preview=text[:500] if len(text) > 500 else text,
                    text_length=len(text),
                )

            # Check for DELIVERATOR canary (new path)
            if DELIVERATOR_CANARY in text:
                try:
                    # Find the JSON object that contains the canary
                    # The canary might be nested (e.g., inside additionalContext)
                    # so find the { that precedes it and match to its closing }
                    canary_pos = text.index(DELIVERATOR_CANARY)
                    # Search backwards for the opening brace
                    start = text.rfind("{", 0, canary_pos)
                    if start == -1:
                        continue
                    # Now find the matching closing brace
                    brace_count = 0
                    end = start
                    for i, c in enumerate(text[start:], start):
                        if c == "{":
                            brace_count += 1
                        elif c == "}":
                            brace_count -= 1
                            if brace_count == 0:
                                end = i + 1
                                break
                    metadata = json.loads(text[start:end])
                    found_blocks.append((metadata, "deliverator"))
                except (ValueError, json.JSONDecodeError) as e:
                    logger.warning(f"Deliverator middleware: failed to parse DELIVERATOR metadata: {e}")
                continue

            # Check for LOOM canary (legacy path)
            if LOOM_CANARY in text:
                # Must be the actual metadata block, not a file diff mentioning it
                if "UserPromptSubmit hook additional context:" not in text:
                    continue
                try:
                    start = text.index("{")
                    end = text.rindex("}") + 1
                    metadata = json.loads(text[start:end])
                    found_blocks.append((metadata, "loom"))
                except (ValueError, json.JSONDecodeError) as e:
                    logger.warning(f"Deliverator middleware: failed to parse LOOM metadata: {e}")
                continue

    if not found_blocks:
        return None

    # Prefer DELIVERATOR blocks over LOOM blocks, take the last one of each type
    deliverator_blocks = [b for b in found_blocks if b[1] == "deliverator"]
    loom_blocks = [b for b in found_blocks if b[1] == "loom"]

    if deliverator_blocks:
        metadata, _ = deliverator_blocks[-1]
        logger.info(f"Deliverator middleware: extracted DELIVERATOR metadata, session={metadata.get('session_id', '?')[:8]}")
        return metadata
    elif loom_blocks:
        metadata, _ = loom_blocks[-1]
        logger.info(f"Deliverator middleware: extracted LOOM metadata (legacy), session={metadata.get('session_id', '?')[:8]}")
        return metadata

    return None


class DeliveratorMiddleware:
    """ASGI middleware that extracts metadata from request bodies and promotes to headers.

    Uses raw ASGI instead of BaseHTTPMiddleware for better control over body handling.
    Runs before Logfire's auto-instrumentation so traceparent shows up in the right place.

    Pizza in thirty minutes or it's free.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Only process POST to messages endpoints
        method = scope.get("method", "")
        path = scope.get("path", "")

        if method != "POST" or "messages" not in path:
            await self.app(scope, receive, send)
            return

        # Collect the request body
        body_chunks = []
        more_body = True

        async def receive_wrapper() -> Message:
            nonlocal more_body
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                if body:
                    body_chunks.append(body)
                more_body = message.get("more_body", False)
            return message

        # Read all body chunks
        while more_body:
            await receive_wrapper()

        body_bytes = b"".join(body_chunks)

        # Try to parse and extract metadata
        metadata = None
        if body_bytes:
            try:
                body = json.loads(body_bytes)
                metadata = extract_metadata_from_body(body)
            except json.JSONDecodeError:
                pass

        # Promote metadata to headers
        headers = list(scope.get("headers", []))

        logfire.debug(
            "Deliverator middleware: processing request",
            path=path,
            body_length=len(body_bytes),
            has_metadata=metadata is not None,
            metadata_keys=list(metadata.keys()) if metadata else [],
        )

        if metadata:
            traceparent = metadata.get("traceparent")
            session_id = metadata.get("session_id")
            pattern = metadata.get("pattern")

            if traceparent:
                headers.append((b"traceparent", traceparent.encode()))
                logfire.info(f"Deliverator middleware: injected traceparent", traceparent=traceparent[:40])
            if session_id:
                headers.append((b"x-session-id", session_id.encode()))
            if pattern:
                headers.append((b"x-loom-pattern", pattern.encode()))

        # Create new scope with updated headers
        new_scope = dict(scope)
        new_scope["headers"] = headers

        # Create a new receive that returns the body we already read
        body_sent = False

        async def new_receive() -> Message:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {
                    "type": "http.request",
                    "body": body_bytes,
                    "more_body": False,
                }
            # After body is sent, return disconnect
            return {"type": "http.disconnect"}

        await self.app(new_scope, new_receive, send)
