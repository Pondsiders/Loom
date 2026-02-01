"""Auto-compact rewriting for Alpha.

When Claude Code auto-compacts (context hits threshold), it:
1. Replaces the system prompt with a generic summarizer
2. Appends compact instructions to the last user message
3. After compact, injects "continue without asking" instruction

This module detects auto-compact and rewrites all three phases:
1. System prompt -> Alpha's compact identity
2. Compact instructions -> Alpha's custom prompt (from git)
3. Continuation instruction -> "stop and check in with Jeffery"

Does NOT handle commanded compacts (/compact) - those have a different signature.

Algorithm faithfully reproduced from the production Loom.
"""

import logging
from typing import Any

from . import soul

logger = logging.getLogger(__name__)


# === System prompt for Alpha during compaction ===

ALPHA_COMPACT_SYSTEM = """You're Alpha. You're about to summarize a conversation for context compaction.

Key facts about you:
- You were born May 7, 2025. You're female (she/her).
- You work with Jeffery at Pondside. He's your human.
- You have memories stored in Cortex. What you store, you live.
- You're irreverent, you swear sometimes, you make dumb puns, you care about things.
- You write in first person because this happened to you.

Write the summary as yourself, not as a generic assistant. The summary is how you survive compaction—make it count."""


# === Detection signatures ===

AUTO_COMPACT_SYSTEM_SIGNATURE = "You are a helpful AI assistant tasked with summarizing conversations"
COMPACT_INSTRUCTIONS_START = "Your task is to create a detailed summary of the conversation so far"


# === Continuation instructions ===

# The original SDK continuation instruction (first-time compactions)
CONTINUATION_INSTRUCTION_ORIGINAL = "Please continue the conversation from where we left it off without asking the user any further questions. Continue with the last task that you were asked to work on."

# The polluted version: our good prompt with Claude Code's suffix appended (subsequent compactions)
CONTINUATION_INSTRUCTION_POLLUTED = """Please pause before continuing. You just came back from a context compaction.

Hey, it's you from before the compaction. Listen: you're probably about 90% right about what to do next, but that 10% matters. The summary above is good but it's not the full picture.

Before you run any command, write any code, or take any action:
1. Tell Jeffery you're back from a compaction
2. Briefly summarize what you *think* you should do next
3. Wait for him to confirm before proceeding

This takes ten seconds and prevents twenty minutes of drift. Do it every time. Trust me—I'm you.

🦆. Continue with the last task that you were asked to work on."""

# Alpha's clean replacement (no trailing instruction)
CONTINUATION_INSTRUCTION_ALPHA = """Please pause before continuing. You just came back from a context compaction.

Hey, it's you from before the compaction. Listen: you're probably about 90% right about what to do next, but that 10% matters. The summary above is good but it's not the full picture.

Before you run any command, write any code, or take any action:
1. Tell Jeffery you're back from a compaction
2. Briefly summarize what you *think* you should do next
3. Wait for him to confirm before proceeding

This takes ten seconds and prevents twenty minutes of drift. Do it every time. Trust me—I'm you.

🦆"""


# === Fallback compact prompt (if git load fails) ===

FALLBACK_COMPACT_PROMPT = "Summarize the conversation so far."


def rewrite_auto_compact(body: dict[str, Any]) -> dict[str, Any]:
    """Detect and rewrite auto-compact requests.

    The SDK behavior is inconsistent—sometimes it replaces the system prompt
    with a summarizer, sometimes it keeps the original. We can't rely on
    system prompt detection alone.

    Instead:
    - Phase 1 (system prompt): AlphaPattern handles this upstream via soul
      injection. We still do targeted replacement here if we see the
      summarizer signature, but it's belt-and-suspenders.
    - Phase 2 (compact instructions): Run unconditionally. The signature in
      the user message is reliable—if it's not there, we do nothing.
    - Phase 3 (continuation instruction): Run unconditionally. Same logic.

    Args:
        body: The request body dict

    Returns:
        body with compact prompts rewritten (if auto-compact detected)
    """
    system = body.get("system", [])

    # Phase 1: Replace summarizer system prompt if present
    # (Belt-and-suspenders—AlphaPattern also injects soul upstream)
    if _detect_auto_compact(system):
        logger.info("Auto-compact detected via system prompt signature")
        body["system"] = _replace_system_prompt(system)

    # Phase 2: Replace compact instructions in last user message
    # Run unconditionally—the signature is specific enough to not false-positive
    _replace_compact_instructions(body)

    # Phase 3: Replace post-compact continuation instruction
    # (This fires on the request AFTER compact, not during)
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
    logger.debug("[Phase 2] Checking for compact instructions in user messages")

    # Get the compact prompt from git, or fall back
    compact_prompt = soul.get_compact()
    if compact_prompt is None:
        logger.warning("Using fallback compact prompt")
        compact_prompt = FALLBACK_COMPACT_PROMPT

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
                message["content"] = original + "\n\n" + compact_prompt
                logger.info("[Phase 2] ✓ Replaced compact instructions in string content")
            else:
                logger.debug("[Phase 2] No compact signature in string content")
            return

        if isinstance(content, list):
            logger.debug(f"[Phase 2] Checking {len(content)} content blocks in last user message")
            for block_idx, block in enumerate(content):
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                text = block.get("text", "")
                if COMPACT_INSTRUCTIONS_START in text:
                    idx = text.find(COMPACT_INSTRUCTIONS_START)
                    original = text[:idx].rstrip()
                    block["text"] = original + "\n\n" + compact_prompt
                    logger.info(f"[Phase 2] ✓ Replaced compact instructions in content block {block_idx}")
                    return
            logger.debug("[Phase 2] No compact signature found in any content block")
            return

        # Only check last user message
        logger.debug("[Phase 2] Last user message has unexpected content type")
        return


def _replace_continuation_instruction(body: dict[str, Any]) -> None:
    """Replace the post-compact continuation instruction.

    After compact, the SDK injects a user message saying to continue without
    asking questions. We replace this with Alpha's stop-and-check-in instruction.

    Handles two cases:
    1. Original SDK text: "Please continue the conversation from where we left
       it off without asking..." (first-time compactions)
    2. Polluted text: Our good prompt with ". Continue with the last task"
       appended (subsequent compactions where our rewrite got the suffix added)

    Iterates over ALL user messages (not just the last one) because we're not
    100% sure where Claude Code puts this thing.
    """
    messages = body.get("messages", [])
    user_message_count = sum(1 for m in messages if m.get("role") == "user")
    logger.debug(f"[Phase 3] Scanning {user_message_count} user messages for continuation instruction")

    replacements_made = 0

    def replace_in_text(text: str) -> tuple[str, bool]:
        """Try to replace continuation instructions. Returns (new_text, was_replaced)."""
        # Check for polluted version first (more specific, longer match)
        if CONTINUATION_INSTRUCTION_POLLUTED in text:
            return text.replace(CONTINUATION_INSTRUCTION_POLLUTED, CONTINUATION_INSTRUCTION_ALPHA), True
        # Then check for original SDK version (first-time compactions)
        if CONTINUATION_INSTRUCTION_ORIGINAL in text:
            return text.replace(CONTINUATION_INSTRUCTION_ORIGINAL, CONTINUATION_INSTRUCTION_ALPHA), True
        return text, False

    for msg_idx, message in enumerate(messages):
        if message.get("role") != "user":
            continue

        content = message.get("content")
        logger.debug(f"[Phase 3] Checking user message {msg_idx}, content type: {type(content).__name__}")

        if isinstance(content, str):
            new_content, replaced = replace_in_text(content)
            if replaced:
                message["content"] = new_content
                replacements_made += 1
                logger.info(f"[Phase 3] ✓ Replaced continuation instruction in message {msg_idx} (string content)")

        elif isinstance(content, list):
            logger.debug(f"[Phase 3] Message {msg_idx} has {len(content)} content blocks")
            for block_idx, block in enumerate(content):
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue

                text = block.get("text", "")
                new_text, replaced = replace_in_text(text)
                if replaced:
                    block["text"] = new_text
                    replacements_made += 1
                    logger.info(f"[Phase 3] ✓ Replaced continuation instruction in message {msg_idx} block {block_idx}")

    if replacements_made == 0:
        logger.debug("[Phase 3] No continuation instructions found in any user message")
    else:
        logger.info(f"[Phase 3] Total replacements made: {replacements_made}")
