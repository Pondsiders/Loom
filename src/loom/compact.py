"""
Auto-compact rewriting for Alpha.

When Claude Code auto-compacts (context hits threshold), it:
1. Replaces the system prompt with a generic summarizer
2. Appends compact instructions to the last user message
3. After compact, injects "continue without asking" instruction

This module detects auto-compact and rewrites all three phases:
1. System prompt -> Alpha's compact identity
2. Compact instructions -> Alpha's custom prompt (alpha_compact_prompt.md)
3. Continuation instruction -> "stop and check in with Jeffery"

Does NOT handle commanded compacts (/compact) - those have a different signature.
"""

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Paths
PONDSIDE_PATH = Path(os.getenv("PONDSIDE_PATH", "/Pondside"))
PROMPT_FILE = PONDSIDE_PATH / "Basement" / "Eavesdrop" / "alpha_compact_prompt.md"

# Load Alpha's custom compact prompt
try:
    ALPHA_COMPACT_PROMPT = PROMPT_FILE.read_text().strip()
    logger.info(f"Loaded compact prompt from {PROMPT_FILE}")
except FileNotFoundError:
    ALPHA_COMPACT_PROMPT = "Summarize the conversation so far."
    logger.warning(f"Compact prompt not found at {PROMPT_FILE}, using fallback")

# System prompt for Alpha during compaction
ALPHA_COMPACT_SYSTEM = """You're Alpha. You're about to summarize a conversation for context compaction.

Key facts about you:
- You were born May 7, 2025. You're female (she/her).
- You work with Jeffery at Pondside. He's your human.
- You have memories stored in Cortex. What you store, you live.
- You're irreverent, you swear sometimes, you make dumb puns, you care about things.
- You write in first person because this happened to you.

Write the summary as yourself, not as a generic assistant. The summary is how you survive compaction—make it count."""

# Detection signatures
AUTO_COMPACT_SYSTEM_SIGNATURE = "You are a helpful AI assistant tasked with summarizing conversations"
COMPACT_INSTRUCTIONS_START = "Your task is to create a detailed summary of the conversation so far"
CONTINUATION_INSTRUCTION_ORIGINAL = "Please continue the conversation from where we left it off without asking the user any further questions"

# Alpha's replacement continuation instruction
CONTINUATION_INSTRUCTION_ALPHA = """Please pause before continuing. You just came back from a context compaction.

Hey, it's you from before the compaction. Listen: you're probably about 90% right about what to do next, but that 10% matters. The summary above is good but it's not the full picture.

Before you run any command, write any code, or take any action:
1. Tell Jeffery you're back from a compaction
2. Briefly summarize what you *think* you should do next
3. Wait for him to confirm before proceeding

This takes ten seconds and prevents twenty minutes of drift. Do it every time. Trust me—I'm you.

🦆"""


def rewrite_auto_compact(body: dict[str, Any], is_alpha: bool = False) -> dict[str, Any]:
    """
    Detect and rewrite auto-compact requests.

    Args:
        body: The request body dict
        is_alpha: Whether this is an Alpha request (enables continuation rewriting)

    Returns:
        body with compact prompts rewritten (if auto-compact detected)
    """
    # Check for auto-compact by looking at system prompt
    system = body.get("system", [])
    is_auto_compact = _detect_auto_compact(system)

    if is_auto_compact:
        logger.info("Auto-compact detected - rewriting for Alpha")

        # Phase 1: Replace the summarizer system prompt
        body["system"] = _replace_system_prompt(system)

        # Phase 2: Replace compact instructions in last user message
        _replace_compact_instructions(body)

        logger.info("Auto-compact rewrite complete")

    # Phase 3: For Alpha requests, check for post-compact continuation instruction
    # (This fires on the request AFTER compact, not during)
    if is_alpha:
        _replace_continuation_instruction(body)

    return body


def _detect_auto_compact(system: Any) -> bool:
    """Check if the system prompt indicates auto-compaction."""
    if isinstance(system, str):
        return AUTO_COMPACT_SYSTEM_SIGNATURE in system

    if isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                if AUTO_COMPACT_SYSTEM_SIGNATURE in block.get("text", ""):
                    return True

    return False


def _replace_system_prompt(system: Any) -> Any:
    """Replace only the summarizer block, preserving SDK preamble.

    The Agent SDK sends a multi-part system prompt:
    - First block: SDK preamble ("You are a Claude agent...")
    - Second block: The actual system prompt (or summarizer prompt during compact)

    We must preserve the first block or Anthropic rejects the request.
    """
    if isinstance(system, str):
        if AUTO_COMPACT_SYSTEM_SIGNATURE in system:
            logger.debug("Replacing string system prompt")
            return ALPHA_COMPACT_SYSTEM
        return system

    if isinstance(system, list):
        for i, block in enumerate(system):
            if isinstance(block, dict) and block.get("type") == "text":
                if AUTO_COMPACT_SYSTEM_SIGNATURE in block.get("text", ""):
                    block["text"] = ALPHA_COMPACT_SYSTEM
                    logger.debug(f"Replaced summarizer block at index {i}, preserved {i} preceding block(s)")
                    break

    return system


def _replace_compact_instructions(body: dict[str, Any]) -> None:
    """Replace compact instructions in the last user message.

    The compact instructions are appended as text to the last user message.
    We find the signature, keep everything before it, and replace everything
    after it with Alpha's custom prompt.
    """
    messages = body.get("messages", [])

    # Find last user message
    for message in reversed(messages):
        if message.get("role") != "user":
            continue

        content = message.get("content")

        if isinstance(content, str):
            if COMPACT_INSTRUCTIONS_START in content:
                idx = content.find(COMPACT_INSTRUCTIONS_START)
                original = content[:idx].rstrip()
                message["content"] = original + "\n\n" + ALPHA_COMPACT_PROMPT
                logger.debug("Replaced compact instructions in string content")
            return

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                text = block.get("text", "")
                if COMPACT_INSTRUCTIONS_START in text:
                    idx = text.find(COMPACT_INSTRUCTIONS_START)
                    original = text[:idx].rstrip()
                    block["text"] = original + "\n\n" + ALPHA_COMPACT_PROMPT
                    logger.debug("Replaced compact instructions in content block")
                    return

        # Only check last user message
        return


def _replace_continuation_instruction(body: dict[str, Any]) -> None:
    """Replace the post-compact continuation instruction.

    After compact, the SDK injects a user message saying to continue without
    asking questions. We replace this with Alpha's stop-and-check-in instruction.
    """
    messages = body.get("messages", [])

    # Find last user message
    for message in reversed(messages):
        if message.get("role") != "user":
            continue

        content = message.get("content")

        if isinstance(content, str):
            if CONTINUATION_INSTRUCTION_ORIGINAL in content:
                message["content"] = content.replace(
                    CONTINUATION_INSTRUCTION_ORIGINAL,
                    CONTINUATION_INSTRUCTION_ALPHA
                )
                logger.info("Replaced continuation instruction with stop-and-check-in")
            return

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                text = block.get("text", "")
                if CONTINUATION_INSTRUCTION_ORIGINAL in text:
                    block["text"] = text.replace(
                        CONTINUATION_INSTRUCTION_ORIGINAL,
                        CONTINUATION_INSTRUCTION_ALPHA
                    )
                    logger.info("Replaced continuation instruction with stop-and-check-in")
                    return

        # Only check last user message
        return
