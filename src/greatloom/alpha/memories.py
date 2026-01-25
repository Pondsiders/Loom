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
    """Format a single memory as an XML-ish block.

    Args:
        memory: Dict with id, created_at, content

    Returns:
        Formatted memory block string
    """
    mem_id = memory.get("id", "?")
    created_at = memory.get("created_at", "")
    content = memory.get("content", "").strip()

    relative_time = format_relative_time(created_at)

    return f"""<memory id={mem_id} created="{relative_time}">
{content}
</memory>"""


def inject_memories(body: dict, metadata: dict) -> None:
    """Inject memories from metadata into the message body.

    Memories are added as separate user message content blocks
    AFTER the actual user message. This puts them closer to where
    I generate my response (attention recency).

    Modifies body in place.

    Args:
        body: The request body dict
        metadata: The extracted metadata containing memories
    """
    memories = metadata.get("memories", [])
    queries = metadata.get("memory_queries", [])

    if not memories:
        return

    logger.info(f"Injecting {len(memories)} memories (queries: {queries})")

    messages = body.get("messages", [])
    if not messages:
        return

    # Find the last user message with actual text content
    # (not just tool_result blocks)
    target_msg_idx = None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "user":
            continue

        content = msg.get("content")
        if isinstance(content, str):
            target_msg_idx = i
            break
        elif isinstance(content, list):
            has_text = any(
                isinstance(b, dict) and b.get("type") == "text"
                for b in content
            )
            if has_text:
                target_msg_idx = i
                break

    if target_msg_idx is None:
        logger.debug("No suitable user message found for memory injection")
        return

    # Format each memory as a content block
    memory_blocks = []
    for mem in memories:
        block_text = format_memory_block(mem)
        memory_blocks.append({"type": "text", "text": block_text})

    # Add a header block with queries
    queries_str = ", ".join(f'"{q}"' for q in queries) if queries else "none"
    header = f"""<memories queries={queries_str}>
The following memories were surfaced by your prompt. Read them, then respond.
"""
    footer = """</memories>
<note>
Memno knows more. Use the Memno agent to ask follow-up questions.
</note>"""

    # Build the injection: header + memories + footer
    injection_blocks = [
        {"type": "text", "text": header},
        *memory_blocks,
        {"type": "text", "text": footer},
    ]

    # Insert AFTER the target user message
    # We do this by adding new user messages after the target
    for block in injection_blocks:
        messages.insert(target_msg_idx + 1, {
            "role": "user",
            "content": [block],
        })
        target_msg_idx += 1  # Keep inserting after the last inserted

    logger.info(f"Injected {len(memories)} memories as {len(injection_blocks)} content blocks")
