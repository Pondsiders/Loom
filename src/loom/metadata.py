"""Metadata extraction from the canary block."""

import json
import logging

logger = logging.getLogger(__name__)

# The canary that marks metadata blocks
CANARY = "EAVESDROP_METADATA_BLOCK_UlVCQkVSRFVDSw"


def extract_metadata(body: dict) -> dict | None:
    """Find and remove the metadata block from the request.

    Returns the extracted metadata, or None if not found.
    Modifies body in place to remove the canary block.
    """
    messages = body.get("messages", [])

    # Search backwards through messages
    for msg_idx in range(len(messages) - 1, -1, -1):
        msg = messages[msg_idx]
        if msg.get("role") != "user":
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        # Search content blocks for the canary
        for block_idx, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "text":
                continue

            text = block.get("text", "")
            if CANARY not in text:
                continue

            # Must be the actual metadata block, not a file diff that mentions the canary
            # The real block has "UserPromptSubmit hook additional context:" as its prefix
            if "UserPromptSubmit hook additional context:" not in text:
                continue

            # Found it! Pop the block
            content.pop(block_idx)
            logger.info(f"Removed canary block {block_idx} from message {msg_idx}")

            # Extract JSON: everything between first { and last }
            try:
                start = text.index("{")
                end = text.rindex("}") + 1
                json_str = text[start:end]
                metadata = json.loads(json_str)
                logger.info(f"Extracted metadata: session={metadata.get('session_id', '?')}")
                return metadata
            except (ValueError, json.JSONDecodeError) as e:
                logger.error(f"Failed to parse metadata JSON: {e}")
                return None

    return None
