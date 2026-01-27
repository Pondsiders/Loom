"""Metadata extraction and timestamp transformation.

The metadata payload arrives in the body as a text block containing JSON.
It's marked with a canary string so we can find it.

Flow:
1. Hook creates metadata JSON with canary and sent_at timestamp
2. Hook outputs it as additionalContext
3. Claude Code appends it to the user message
4. Deliverator promotes some fields to headers
5. Loom extracts full metadata, transforms blocks to timestamps

This module handles the extraction and transformation.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# The canary that marks our metadata block
DELIVERATOR_CANARY = "DELIVERATOR_METADATA_UlVCQkVSRFVDSw"


def extract_metadata(body: dict) -> tuple[dict | None, dict]:
    """Extract metadata from body and transform metadata blocks to timestamps.

    Searches for text blocks containing the canary, extracts the JSON,
    then replaces each block with a human-readable timestamp.

    Iterates top-to-bottom so the last (most recent) metadata wins.

    Args:
        body: The request body dict

    Returns:
        (metadata, transformed_body) - metadata dict from last block (or None),
        body with metadata blocks transformed to timestamps
    """
    messages = body.get("messages", [])
    if not messages:
        return None, body

    metadata = None
    transforms = []  # List of (msg_idx, block_idx or None, sent_at) to apply

    # Search messages top-to-bottom, collect all canary blocks
    for msg_idx, msg in enumerate(messages):
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
                        block_metadata = json.loads(content[start:end])
                        sent_at = block_metadata.get("sent_at", "")
                        transforms.append((msg_idx, None, sent_at))
                        metadata = block_metadata
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
                            block_metadata = json.loads(text[start:end])
                            sent_at = block_metadata.get("sent_at", "")
                            transforms.append((msg_idx, block_idx, sent_at))
                            metadata = block_metadata
                            logger.debug(f"Found metadata in message {msg_idx} block {block_idx}")
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse metadata JSON: {e}")

    # Apply transforms - replace each metadata block with its timestamp
    # Process in reverse order so indices stay valid
    for msg_idx, block_idx, sent_at in reversed(transforms):
        msg = messages[msg_idx]

        if sent_at:
            timestamp_text = f"[Sent {sent_at}]"
        else:
            # No sent_at in metadata - just remove the block
            timestamp_text = None

        if block_idx is None:
            # Entire message content is the metadata string
            if timestamp_text:
                msg["content"] = timestamp_text
                logger.debug(f"Transformed message {msg_idx} to timestamp")
            else:
                messages.pop(msg_idx)
                logger.debug(f"Removed message {msg_idx} (no sent_at)")
        else:
            # Transform just the block
            content = msg.get("content", [])
            if isinstance(content, list) and block_idx < len(content):
                if timestamp_text:
                    content[block_idx]["text"] = timestamp_text
                    logger.debug(f"Transformed block {block_idx} in message {msg_idx}")
                else:
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
            f"memories={len(metadata.get('memories', []))}, "
            f"transforms={len(transforms)}"
        )

    return metadata, body


# Legacy alias for compatibility during transition
extract_and_strip_metadata = extract_metadata
