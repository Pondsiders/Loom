"""Metadata extraction and stripping.

The metadata payload arrives in the body as a text block containing JSON.
It's marked with a canary string so we can find it.

Flow:
1. Hook creates metadata JSON with canary
2. Hook outputs it as additionalContext
3. Claude Code appends it to the user message
4. Deliverator promotes some fields to headers
5. Loom extracts full metadata from body, uses it, then strips it

This module handles the extraction and stripping.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# The canary that marks our metadata block
DELIVERATOR_CANARY = "DELIVERATOR_METADATA_UlVCQkVSRFVDSw"


def extract_and_strip_metadata(body: dict) -> tuple[dict | None, dict]:
    """Extract metadata from body and strip the metadata block.

    Searches for a text block containing the canary, extracts the JSON,
    then removes that block from the body.

    Args:
        body: The request body dict

    Returns:
        (metadata, cleaned_body) - metadata dict or None, body with metadata stripped
    """
    messages = body.get("messages", [])
    if not messages:
        return None, body

    metadata = None
    block_to_remove = None  # (msg_idx, block_idx) or (msg_idx, None for string content)

    # Search messages BACKWARDS for the canary - we want the MOST RECENT metadata
    # because each turn adds a new metadata block, and old ones stay in history
    for msg_idx in range(len(messages) - 1, -1, -1):
        msg = messages[msg_idx]
        if msg.get("role") != "user":
            continue

        content = msg.get("content")

        if isinstance(content, str):
            if DELIVERATOR_CANARY in content:
                # Find the JSON
                try:
                    start = content.find("{")
                    end = content.rfind("}") + 1
                    if start != -1 and end > start:
                        metadata = json.loads(content[start:end])
                        block_to_remove = (msg_idx, None)
                        logger.debug(f"Found metadata in message {msg_idx} (string content)")
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse metadata JSON: {e}")

        elif isinstance(content, list):
            for block_idx, block in enumerate(content):
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                text = block.get("text", "")
                if DELIVERATOR_CANARY in text:
                    try:
                        start = text.find("{")
                        end = text.rfind("}") + 1
                        if start != -1 and end > start:
                            metadata = json.loads(text[start:end])
                            block_to_remove = (msg_idx, block_idx)
                            logger.debug(f"Found metadata in message {msg_idx} block {block_idx}")
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse metadata JSON: {e}")
                    break

        if metadata:
            break

    # Strip the metadata block
    if block_to_remove:
        msg_idx, block_idx = block_to_remove
        msg = messages[msg_idx]

        if block_idx is None:
            # Entire message content is the metadata string
            # Remove the whole message
            messages.pop(msg_idx)
            logger.debug(f"Removed entire message {msg_idx} (was metadata only)")
        else:
            # Remove just the block
            content = msg.get("content", [])
            if isinstance(content, list) and block_idx < len(content):
                content.pop(block_idx)
                # If message is now empty, remove it
                if not content:
                    messages.pop(msg_idx)
                    logger.debug(f"Removed empty message {msg_idx}")
                else:
                    msg["content"] = content
                    logger.debug(f"Removed block {block_idx} from message {msg_idx}")

    if metadata:
        session_id = metadata.get("session_id", "")
        logger.info(
            f"Extracted metadata: session={session_id[:8] if session_id else 'none'}, "
            f"memories={len(metadata.get('memories', []))}"
        )

    return metadata, body
