"""Intro - the inner voice that notices what's memorable.

Intro watches conversations and stores memorable items in Redis.
This module reads those memorables and injects them into the
user message so Alpha knows to store them.
"""

import logging
import os

import redis.asyncio as redis

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://alpha-pi:6379")


async def _get_redis() -> redis.Redis:
    """Get async Redis connection."""
    return redis.from_url(REDIS_URL, decode_responses=True)


async def get_memorables(session_id: str) -> list[str]:
    """Get current memorables for a session.

    Args:
        session_id: The session ID to look up

    Returns:
        List of memorable strings, or empty list if none
    """
    if not session_id:
        return []

    try:
        r = await _get_redis()
        key = f"intro:memorables:{session_id}"
        memorables = await r.lrange(key, 0, -1)
        await r.aclose()

        if memorables:
            logger.info(f"Found {len(memorables)} memorables for session {session_id[:8]}")
        return memorables
    except Exception as e:
        logger.error(f"Error reading memorables: {e}")
        return []


def format_block(memorables: list[str]) -> str:
    """Format memorables as an <intro> block for injection.

    Args:
        memorables: List of memorable strings from Intro

    Returns:
        Formatted string ready for injection, or empty string if no memorables
    """
    if not memorables:
        return ""

    # Clean up memorables - strip backticks and empty lines
    cleaned = []
    for mem in memorables:
        mem = mem.strip()
        # Skip empty lines and lone backticks
        if not mem or mem == "```":
            continue
        # Strip leading/trailing backticks from the whole string
        mem = mem.strip("`")
        if mem:
            cleaned.append(mem)

    if not cleaned:
        return ""

    lines = ["Alpha, this is your inner voice. Store these now:"]
    for mem in cleaned:
        lines.append(f"- {mem}")

    return "\n".join(lines)


def inject_as_final_message(body: dict, session_id: str, block: str) -> None:
    """Inject memorables block as the final user message.

    Modifies body in place. Adds a NEW user message at the very end
    containing the formatted memorables. This puts Intro's suggestions
    closest to response generation (maximum attention recency).

    Args:
        body: The full request body dict (modified in place)
        session_id: Session ID for logging
        block: Formatted memorables string
    """
    if not block:
        return

    messages = body.get("messages", [])
    if not messages:
        return

    # Add as the final user message
    messages.append({
        "role": "user",
        "content": [{"type": "text", "text": block}],
    })

    logger.info(f"Injected Intro as final message for session {session_id[:8]}")
