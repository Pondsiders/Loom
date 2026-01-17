"""Intro integration - read memorables from Redis, inject into requests.

Intro watches conversations and notices what's memorable. This module reads
those memorables and formats them for injection into the Loom's request flow.
"""

import logging
import os

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://alpha-pi:6379")
_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Get or create async Redis connection."""
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


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
        r = await get_redis()
        key = f"intro:memorables:{session_id}"
        memorables = await r.lrange(key, 0, -1)
        if memorables:
            logger.info(f"Found {len(memorables)} memorables for session {session_id[:8]}")
        return memorables
    except Exception as e:
        logger.error(f"Error reading memorables: {e}")
        return []


def format_memorables_block(memorables: list[str]) -> str:
    """Format memorables as a <subvox> block for injection.

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

    lines = ["Intro surfaced these moments. Store what lands, let the rest go:"]
    for mem in cleaned:
        lines.append(f"- {mem}")

    return "<subvox>\n" + "\n".join(lines) + "\n</subvox>"


def inject_memorables(request_body: dict, session_id: str, memorables_block: str) -> dict:
    """Inject memorables block into the request body.

    Adds a new user message content block (type: text) containing the
    formatted memorables. This appears after the actual user message.

    Args:
        request_body: The full request body dict
        session_id: Session ID for logging
        memorables_block: Formatted memorables string

    Returns:
        Modified request body with memorables injected
    """
    if not memorables_block:
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

            # Append the memorables block as a new text content block
            content.append({
                "type": "text",
                "text": memorables_block,
            })

            messages[i]["content"] = content
            logger.info(f"Injected memorables into user message for session {session_id[:8]}")
            break

    return request_body
