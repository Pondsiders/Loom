"""
Hippo integration - wait for and inject memory retrieval results.

Intro processes user prompts and writes memory search results to Redis.
This module waits for those results and injects them into the request.

The key insight: trace_id is the rendezvous point. The hook emits an event
with the trace_id, Intro processes it and writes to hippo:{trace_id},
and we BLPOP here to wait for the result.
"""

import logging
import os

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://alpha-pi:6379")
_redis: aioredis.Redis | None = None

# How long to wait for Intro to finish (seconds)
HIPPO_TIMEOUT = 8.0


async def get_redis() -> aioredis.Redis:
    """Get or create async Redis connection."""
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def wait_for_memories(trace_id: str) -> str | None:
    """
    Wait for Intro to finish processing and return the memory injection text.

    Uses BLPOP to block until Intro writes to hippo:{trace_id}, or timeout.

    Args:
        trace_id: The trace ID to wait for

    Returns:
        The formatted memory text to inject, or None if timeout/error/empty
    """
    if not trace_id:
        return None

    key = f"hippo:{trace_id}"

    try:
        r = await get_redis()

        # BLPOP returns (key, value) or None on timeout
        result = await r.blpop(key, timeout=HIPPO_TIMEOUT)

        if result is None:
            logger.warning(f"Hippo timeout waiting for {key}")
            return None

        _, value = result

        # Empty string means Intro processed but found nothing
        if not value or not value.strip():
            logger.debug(f"Hippo returned empty for {key}")
            return None

        logger.info(f"Got {len(value)} chars from Hippo for trace {trace_id[:8]}")
        return value

    except Exception as e:
        logger.error(f"Error waiting for Hippo: {e}")
        return None


def inject_hippo_memories(request_body: dict, memories_text: str) -> dict:
    """
    Inject Hippo memory text into the request body.

    Adds a new text content block to the last user message.

    Args:
        request_body: The full request body dict
        memories_text: The formatted memories string from Intro

    Returns:
        Modified request body with memories injected
    """
    if not memories_text:
        return request_body

    messages = request_body.get("messages", [])
    if not messages:
        return request_body

    # Find the last user message
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            content = messages[i].get("content", [])

            # If content is a string, convert to list format
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]

            # Append the memories as a new text content block
            content.append({
                "type": "text",
                "text": memories_text,
            })

            messages[i]["content"] = content
            logger.info(f"Injected Hippo memories into user message")
            break

    return request_body
