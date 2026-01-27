"""Token counting for context window awareness.

Hits Anthropic's /v1/messages/count_tokens endpoint (free, rate-limited)
and stashes the result in Redis for Duckpond to display.

Fire-and-forget: this runs in a background task and doesn't block requests.
"""

import asyncio
import json
import logging
import os
from typing import Any

import httpx
import redis

logger = logging.getLogger(__name__)

# Redis connection for stashing results
REDIS_URL = os.environ.get("REDIS_URL", "redis://alpha-pi:6379")

# Anthropic API for token counting
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages/count_tokens"


async def count_and_stash(body: dict[str, Any], session_id: str) -> None:
    """Count tokens in the request body and stash in Redis.

    This is designed to be called via asyncio.create_task() - fire and forget.
    Errors are logged but don't propagate.

    Args:
        body: The request body (same format as /v1/messages)
        session_id: Session ID for Redis key
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set, skipping token count")
        return

    if not session_id:
        logger.debug("No session_id, skipping token count")
        return

    try:
        # Build the count_tokens request
        # It takes the same body as /v1/messages but only needs model, messages, and system
        count_body = {
            "model": body.get("model", "claude-sonnet-4-20250514"),
            "messages": body.get("messages", []),
        }

        # Include system prompt if present
        if "system" in body:
            count_body["system"] = body["system"]

        # Include tools if present (they count toward context)
        if "tools" in body:
            count_body["tools"] = body["tools"]

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                ANTHROPIC_API_URL,
                json=count_body,
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )

            if response.status_code == 200:
                data = response.json()
                input_tokens = data.get("input_tokens")

                if input_tokens is not None:
                    # Stash in Redis for Duckpond to read
                    await _stash_to_redis(session_id, input_tokens)
                    logger.info(f"Token count: {input_tokens} for session {session_id[:8]}")
                else:
                    logger.warning("No input_tokens in response")
            else:
                logger.warning(f"Token count API returned {response.status_code}: {response.text[:200]}")

    except httpx.TimeoutException:
        logger.warning("Token count API timed out")
    except Exception as e:
        logger.warning(f"Token count failed: {e}")


async def _stash_to_redis(session_id: str, input_tokens: int) -> None:
    """Stash token count in Redis for Duckpond to read.

    Uses the same key format as the old Eavesdrop: duckpond:context:{session_id}
    """
    try:
        # Use sync redis in a thread pool to avoid blocking
        def _sync_stash():
            r = redis.from_url(REDIS_URL)
            data = json.dumps({
                "input_tokens": input_tokens,
                "timestamp": None,  # Could add pendulum.now() if we want
            })
            # Expire after 1 hour - if session is stale, don't keep the data
            r.set(f"duckpond:context:{session_id}", data, ex=3600)

        await asyncio.to_thread(_sync_stash)

    except Exception as e:
        logger.warning(f"Failed to stash token count to Redis: {e}")
