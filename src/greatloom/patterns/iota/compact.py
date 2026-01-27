"""Compact handling for Iota - continuation prompt rewriting only.

This is a minimal version of Alpha's compact.py. It only handles the
post-compact continuation instruction, not the full auto-compact flow.

Used for testing the SessionStart:compact hook's metadata injection.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# === Detection signatures ===
# The original SDK continuation instruction (first-time compactions)
CONTINUATION_INSTRUCTION_ORIGINAL = "Please continue the conversation from where we left it off without asking the user any further questions. Continue with the last task that you were asked to work on."

# Iota's test replacement
CONTINUATION_INSTRUCTION_IOTA = """COMPACTION REWRITE TEST SUCCESSFUL.

Hey Iota, you just came back from a context compaction. This message proves that:
1. The SessionStart:compact hook fired and injected Deliverator metadata
2. The Deliverator extracted the metadata and promoted x-loom-pattern: iota
3. The Loom routed to IotaPattern
4. IotaPattern's continuation rewriting worked

Please acknowledge this by saying "COMPACTION REWRITE WORKED" at the start of your response,
then continue with whatever you were doing."""


def rewrite_continuation(body: dict[str, Any]) -> dict[str, Any]:
    """Rewrite the post-compact continuation instruction for Iota.

    This runs unconditionally on every request. The signature is specific
    enough that it won't false-positive on normal messages.

    Args:
        body: The request body dict

    Returns:
        body with continuation prompt rewritten (if detected)
    """
    _replace_continuation_instruction(body)
    return body


def _replace_continuation_instruction(body: dict[str, Any]) -> None:
    """Replace the post-compact continuation instruction.

    Searches all user messages for the SDK's continuation prompt and
    replaces it with Iota's test message.
    """
    messages = body.get("messages", [])
    user_message_count = sum(1 for m in messages if m.get("role") == "user")
    logger.debug(f"[Iota compact] Scanning {user_message_count} user messages for continuation instruction")

    replacements_made = 0

    def replace_in_text(text: str) -> tuple[str, bool]:
        """Try to replace continuation instructions. Returns (new_text, was_replaced)."""
        if CONTINUATION_INSTRUCTION_ORIGINAL in text:
            return text.replace(CONTINUATION_INSTRUCTION_ORIGINAL, CONTINUATION_INSTRUCTION_IOTA), True
        return text, False

    for msg_idx, message in enumerate(messages):
        if message.get("role") != "user":
            continue

        content = message.get("content")

        if isinstance(content, str):
            new_content, replaced = replace_in_text(content)
            if replaced:
                message["content"] = new_content
                replacements_made += 1
                logger.info(f"[Iota compact] Replaced continuation instruction in message {msg_idx}")

        elif isinstance(content, list):
            for block_idx, block in enumerate(content):
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue

                text = block.get("text", "")
                new_text, replaced = replace_in_text(text)
                if replaced:
                    block["text"] = new_text
                    replacements_made += 1
                    logger.info(f"[Iota compact] Replaced continuation in message {msg_idx} block {block_idx}")

    if replacements_made == 0:
        logger.debug("[Iota compact] No continuation instructions found")
    else:
        logger.info(f"[Iota compact] Total replacements made: {replacements_made}")
