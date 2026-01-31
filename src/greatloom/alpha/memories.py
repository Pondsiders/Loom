"""Memory injection - surfaces relevant memories after user messages.

Memories arrive in the metadata payload from the hook. This module:
1. Extracts memories from the metadata
2. Formats them with human-friendly timestamps
3. Injects them as content blocks AFTER the user message

The flow: user says something → memories remind me of context → I respond.
Attention recency means memories closer to response generation might help.
"""

import logging
from datetime import datetime
from typing import Any

import pendulum

logger = logging.getLogger(__name__)


def format_relative_time(created_at: str) -> str:
    """Format a timestamp as a human-friendly relative time.

    Examples:
        "2026-01-26T15:00:00Z" → "today at 3:00 PM"
        "2026-01-25T10:30:00Z" → "yesterday at 10:30 AM"
        "2026-01-20T12:00:00Z" → "6 days ago"
        "2025-12-15T08:00:00Z" → "Mon Dec 15 2025"
    """
    try:
        # Parse the timestamp
        dt = pendulum.parse(created_at)
        now = pendulum.now(dt.timezone or "America/Los_Angeles")

        # Calculate the difference
        diff = now.diff(dt)

        if diff.in_days() == 0:
            return f"today at {dt.format('h:mm A')}"
        elif diff.in_days() == 1:
            return f"yesterday at {dt.format('h:mm A')}"
        elif diff.in_days() < 7:
            return f"{diff.in_days()} days ago"
        elif diff.in_days() < 30:
            weeks = diff.in_days() // 7
            return f"{weeks} week{'s' if weeks > 1 else ''} ago"
        else:
            # Use PSO-8601 format for older memories
            return dt.format("ddd MMM D YYYY")

    except Exception as e:
        logger.warning(f"Failed to parse timestamp '{created_at}': {e}")
        return created_at  # Fall back to raw timestamp


def format_memory_block(memory: dict) -> str:
    """Format a single memory as a plain text block.

    Args:
        memory: Dict with id, created_at, content, query (optional)

    Returns:
        Formatted memory block string
    """
    mem_id = memory.get("id", "?")
    created_at = memory.get("created_at", "")
    content = memory.get("content", "").strip()
    query = memory.get("query")

    relative_time = format_relative_time(created_at)

    # If we have the triggering query, use it as the header
    if query:
        return f""""{query}": Memory #{mem_id} ({relative_time})
{content}"""
    else:
        return f"""Memory #{mem_id} ({relative_time}):
{content}"""


def inject_memories(body: dict, metadata: dict) -> None:
    """Inject memories from metadata into the message body.

    Memories are appended to the SAME text block as the user's prompt,
    separated by blank lines. This keeps the message structure simple
    and preserves cache_control attributes on the block.

    Memories are DURABLE: on future turns, unwrap_structured_input()
    will preserve them in context rather than stripping them.

    Modifies body in place.

    Args:
        body: The request body dict
        metadata: The extracted metadata containing memories
    """
    mems = metadata.get("memories", [])

    if not mems:
        return

    logger.info(f"Injecting {len(mems)} memories into current turn")

    messages = body.get("messages", [])
    if not messages:
        return

    # Find the last user message with actual text content
    # (not just tool_result blocks)
    target_msg = None
    target_block = None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "user":
            continue

        content = msg.get("content")
        if isinstance(content, str):
            # Convert string to block format so we can append
            msg["content"] = [{"type": "text", "text": content}]
            target_msg = msg
            target_block = msg["content"][0]
            break
        elif isinstance(content, list):
            # Find the last text block in this message
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "text":
                    target_msg = msg
                    target_block = block
                    break
            if target_block:
                break

    if target_block is None:
        logger.debug("No suitable user message found for memory injection")
        return

    # Format memories and append to the text block
    memory_texts = [format_memory_block(mem) for mem in mems]

    # Append to existing text with blank line separator
    existing_text = target_block.get("text", "")
    target_block["text"] = existing_text + "\n\n" + "\n\n".join(memory_texts)

    logger.info(f"Appended {len(mems)} memories to user message")
