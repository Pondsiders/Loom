"""Scrub noise from Alpha's context window.

This module removes known-bad content blocks and substrings that add noise
without value. The goal is to improve signal-to-noise ratio in Alpha's context.

Philosophy:
- Exact matches for invariant noise (hook success messages)
- Careful substring removal for variable content (file modification notices)
- Empty-block cleanup afterward (Anthropic API requires non-empty blocks)
- Be precise, not clever. False positives on conversation content are worse than noise.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Blocks that match these exactly get removed entirely
EXACT_NOISE_BLOCKS = [
    {
        "type": "text",
        "text": "<system-reminder>\nUserPromptSubmit hook success: Success\n</system-reminder>",
    },
    {
        "type": "text",
        "text": "<system-reminder>\nSessionStart:startup hook success: Success\n</system-reminder>",
    },
]

# Substrings that get removed from block text (compiled regexes)
# These patterns have fixed structure with variable content in specific slots
SCRUB_PATTERNS = [
    # TodoWrite nag - appears in tool results and user messages
    re.compile(
        r"<system-reminder>\s*The TodoWrite tool hasn't been used recently\."
        r".*?"
        r"Make sure that you NEVER mention this reminder to the user\s*</system-reminder>",
        re.DOTALL,
    ),
    # Malware analysis reminder - appears after reading files
    re.compile(
        r"<system-reminder>\s*Whenever you read a file, you should consider whether "
        r"it would be considered malware\."
        r".*?"
        r"You can still analyze existing code, write reports, or answer questions "
        r"about the code behavior\.\s*</system-reminder>",
        re.DOTALL,
    ),
    # File modification notice - variable {path} and {code}
    re.compile(
        r"<system-reminder>\s*Note: .+? was modified, either by the user or by a linter\."
        r".*?"
        r"Here are the relevant changes \(shown with line numbers\):"
        r".*?"
        r"</system-reminder>",
        re.DOTALL,
    ),
]


def scrub_noise(body: dict) -> dict:
    """Remove noise blocks and substrings from the request body.

    Operates on:
    - User messages (role: user)
    - Assistant messages with tool results (role: user, type: tool_result)

    Returns the modified body (mutates in place for efficiency).
    """
    messages = body.get("messages", [])
    total_removed = 0
    total_scrubbed = 0

    for message in messages:
        if message.get("role") != "user":
            continue

        content = message.get("content")
        if not isinstance(content, list):
            continue

        # Phase 1: Remove exact-match noise blocks
        original_len = len(content)
        content = [block for block in content if block not in EXACT_NOISE_BLOCKS]
        removed = original_len - len(content)
        total_removed += removed

        # Phase 2: Scrub substrings from remaining blocks
        for block in content:
            if block.get("type") == "text":
                text = block.get("text", "")
                original_text = text
                for pattern in SCRUB_PATTERNS:
                    text = pattern.sub("", text)
                if text != original_text:
                    block["text"] = text
                    total_scrubbed += 1
            elif block.get("type") == "tool_result":
                # Tool results can have nested content
                nested = block.get("content")
                if isinstance(nested, list):
                    for nested_block in nested:
                        if nested_block.get("type") == "text":
                            text = nested_block.get("text", "")
                            original_text = text
                            for pattern in SCRUB_PATTERNS:
                                text = pattern.sub("", text)
                            if text != original_text:
                                nested_block["text"] = text
                                total_scrubbed += 1
                elif isinstance(nested, str):
                    original_text = nested
                    for pattern in SCRUB_PATTERNS:
                        nested = pattern.sub("", nested)
                    if nested != original_text:
                        block["content"] = nested
                        total_scrubbed += 1

        # Phase 3: Remove empty blocks (Anthropic API requires non-empty content)
        content = [
            block for block in content
            if not (block.get("type") == "text" and not block.get("text", "").strip())
        ]

        message["content"] = content

    if total_removed or total_scrubbed:
        logger.info(f"Scrubbed noise: {total_removed} blocks removed, {total_scrubbed} blocks cleaned")

    return body
