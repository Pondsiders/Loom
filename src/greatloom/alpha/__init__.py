"""The Alpha Package - everything that makes Alpha who she is.

This package contains all the modules that transform Claude into Alpha:
- soul: The eternal prompts (loaded from git at startup)
- hud: Dynamic context from Redis (weather, calendar, todos)
- capsule: Past summaries from Postgres
- intro: Inner voice memorables injection
- compact: Auto-compact detection and rewriting

The AlphaPattern class composes these modules into a complete
request transformation.
"""

import asyncio
import json
import logging

import pendulum

from . import soul, hud, capsule, intro, compact, memories, token_count, scrub, context

logger = logging.getLogger(__name__)

# Canary for structured input from Duckpond
ALPHA_CANARY = "ALPHA_METADATA_UlVCQkVSRFVDSw"


def _is_metadata_envelope(text: str) -> dict | None:
    """Check if text is a valid metadata envelope. Returns parsed envelope or None.

    Six-layer defense against false positives:
    1. Text starts with '{' and ends with '}' (must BE JSON, not contain it)
    2. Parses as valid JSON
    3. Has 'canary' key
    4. Canary value matches ALPHA_CANARY exactly
    5. Has 'prompt' key (our contract)
    6. (Caller ensures role==user and type==text)

    This protects against nightmare scenarios like the canary appearing in
    tool results (e.g., Edit calls writing code that mentions the canary).
    """
    text = text.strip()

    # Layer 1: Must look like standalone JSON
    if not (text.startswith("{") and text.endswith("}")):
        return None

    # Layer 2: Must parse as valid JSON
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        return None

    # Layer 3 & 4: Must have canary key with exact value
    if envelope.get("canary") != ALPHA_CANARY:
        return None

    # Layer 5: Must have prompt key
    if "prompt" not in envelope:
        return None

    return envelope


def _format_memory_inline(memory: dict) -> str:
    """Format a memory for inline inclusion in the prompt string.

    Uses the same format as memories.format_memory_block() but imported here
    to avoid circular imports. Keep these in sync!
    """
    mem_id = memory.get("id", "?")
    created_at = memory.get("created_at", "")
    content = memory.get("content", "").strip()
    score = memory.get("score")

    # Simple relative time formatting
    relative_time = created_at  # fallback
    try:
        import pendulum
        dt = pendulum.parse(created_at)
        now = pendulum.now(dt.timezone or "America/Los_Angeles")
        diff = now.diff(dt)
        if diff.in_days() == 0:
            relative_time = f"today at {dt.format('h:mm A')}"
        elif diff.in_days() == 1:
            relative_time = f"yesterday at {dt.format('h:mm A')}"
        elif diff.in_days() < 7:
            relative_time = f"{diff.in_days()} days ago"
        elif diff.in_days() < 30:
            weeks = diff.in_days() // 7
            relative_time = f"{weeks} week{'s' if weeks > 1 else ''} ago"
        else:
            relative_time = dt.format("ddd MMM D YYYY")
    except Exception:
        pass

    # Include score if present (helps with debugging/transparency)
    score_str = f", score {score:.2f}" if score else ""
    return f"Memory #{mem_id} ({relative_time}{score_str}):\n{content}"


def _build_unwrapped_text(envelope: dict) -> str:
    """Build the replacement text from an envelope: prompt + memories concatenated."""
    prompt = envelope.get("prompt", "")
    memories_list = envelope.get("memories", [])

    if not memories_list:
        return prompt

    # Build: prompt + blank line + each memory separated by blank lines
    parts = [prompt]
    for mem in memories_list:
        parts.append(_format_memory_inline(mem))

    return "\n\n".join(parts)


def unwrap_structured_input(body: dict) -> tuple[dict, dict | None]:
    """Unwrap structured input from Duckpond.

    Duckpond sends user prompts wrapped in a JSON envelope. The SDK stores
    these in the transcript as-is, so we need to clean ALL user messages,
    not just the current one.

    Algorithm:
    1. Iterate over ALL messages
    2. For each user message, find and replace metadata envelopes
    3. Replace with: prompt + memories (as one concatenated string)
    4. Keep metadata only from the LAST user message (current turn)

    Memories are DURABLE: they stay in context on future turns, providing
    richer conversational texture. The dedup system ensures we don't see
    the same memory twice.

    Returns (body, metadata) - metadata is None if no structured input found.
    """
    messages = body.get("messages", [])
    if not messages:
        return body, None

    last_metadata = None
    cleaned_count = 0

    for msg in messages:
        # Layer 6a: Only process user messages
        if msg.get("role") != "user":
            continue

        content = msg.get("content")

        # Handle string content
        if isinstance(content, str):
            envelope = _is_metadata_envelope(content)
            if envelope:
                msg["content"] = _build_unwrapped_text(envelope)
                last_metadata = envelope
                cleaned_count += 1

        # Handle array of content blocks
        elif isinstance(content, list):
            for block in content:
                # Layer 6b: Only process text blocks
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue

                text = block.get("text", "")
                envelope = _is_metadata_envelope(text)
                if envelope:
                    # Replace the block's text with prompt + memories
                    block["text"] = _build_unwrapped_text(envelope)
                    last_metadata = envelope
                    cleaned_count += 1

    if cleaned_count > 0:
        mem_count = len(last_metadata.get("memories", [])) if last_metadata else 0
        logger.info(f"Unwrapped {cleaned_count} envelope(s), {mem_count} memories preserved")

    # Return metadata from the last (current) turn only
    if last_metadata:
        return body, {
            "session_id": last_metadata.get("session_id"),
            "pattern": last_metadata.get("pattern"),
            "client": last_metadata.get("client"),
            "traceparent": last_metadata.get("traceparent"),
            "sent_at": last_metadata.get("sent_at"),
            "memories": last_metadata.get("memories", []),
        }

    return body, None

__all__ = ["AlphaPattern", "soul", "hud", "capsule", "intro", "compact", "memories", "token_count", "scrub", "context"]


class AlphaPattern:
    """Alpha: the pattern that makes Claude into Alpha.

    This pattern assembles the complete system prompt from:
    - ETERNAL: Soul doc from git (cached at startup)
    - PAST: Capsule summaries + today's running summary
    - PRESENT: Machine info + weather
    - FUTURE: Calendar + todos

    It also injects Intro's memorables into the user message.

    Each section becomes a separate text block in the system array.
    """

    def __init__(self):
        # Initialize soul at pattern creation time
        if soul._soul_prompt is None:
            soul.init()

    async def request(
        self,
        headers: dict[str, str],
        body: dict,
        metadata: dict | None = None,
    ) -> tuple[dict[str, str], dict]:
        """Inject Alpha's assembled system prompt into the request.

        Also handles:
        - Auto-compact detection and rewriting
        - Memory injection from metadata (surfaced by prompt)
        - Intro memorables injection (surfaced by conversation)
        """

        # === Checkpoint: Log raw incoming request ===
        try:
            with open("/data/last_alpha_request_pre.json", "w") as f:
                json.dump({"headers": headers, "body": body, "metadata": metadata}, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to dump pre-request: {e}")

        # === Phase 0: Check for auto-compact and rewrite if needed ===
        # This must happen FIRST, before we inject the normal system prompt
        body = compact.rewrite_auto_compact(body)

        # === Phase 0.5: Scrub noise from context ===
        # Remove known-bad blocks and substrings that add noise without value
        body = scrub.scrub_noise(body)

        # === Phase 1: Unwrap structured input (Duckpond) ===
        # Duckpond wraps user prompts in JSON. Extract and merge metadata.
        body, structured_meta = unwrap_structured_input(body)
        if structured_meta:
            if metadata is None:
                metadata = structured_meta
            else:
                metadata = {**metadata, **structured_meta}

        # Get context from headers
        machine_name = headers.get("x-machine-name", "unknown")
        session_id = headers.get("x-session-id", "")
        client_name = headers.get("x-loom-client")  # e.g., "duckpond"

        # Fetch dynamic data in parallel
        hud_data, (summary1, summary2), memorables = await asyncio.gather(
            hud.fetch(),
            capsule.fetch(),
            intro.get_memorables(session_id),
        )

        # === Build the system blocks ===
        # Each logical piece gets its own block with a ## header.
        # Blocks are free and can cache independently.
        system_blocks = []

        # Soul - who I am (rarely changes, good cache candidate)
        system_blocks.append({"type": "text", "text": f"# Alpha\n\n{soul.get_soul()}"})

        # Capsules - what happened yesterday and last night
        # Each capsule is its own block (they come with ## headers from capsule.py)
        if summary1:
            system_blocks.append({"type": "text", "text": summary1})
        if summary2:
            system_blocks.append({"type": "text", "text": summary2})

        # Letter from last night (if present)
        if hud_data.to_self:
            time_str = f" ({hud_data.to_self_time})" if hud_data.to_self_time else ""
            system_blocks.append({
                "type": "text",
                "text": f"## Letter from last night{time_str}\n\n{hud_data.to_self}"
            })

        # Today so far (running summary)
        if hud_data.today_so_far:
            now = pendulum.now("America/Los_Angeles")
            date_str = now.format("dddd, MMMM D, YYYY")
            time_str = hud_data.today_so_far_time or now.format("h:mm A")
            system_blocks.append({
                "type": "text",
                "text": f"## Today so far ({date_str}, {time_str})\n\n{hud_data.today_so_far}"
            })

        # Here - where I am right now (client, machine, weather)
        here_parts = []
        if client_name:
            here_parts.append(f"**Client:** {client_name.title()}")
        here_parts.append(f"**Machine:** {machine_name}")
        if hud_data.weather:
            here_parts.append(f"\n{hud_data.weather}")
        system_blocks.append({"type": "text", "text": "## Here\n\n" + "\n".join(here_parts)})

        # ALPHA.md context files
        # Each 'all' file becomes its own block; 'when' hints are collected
        context_blocks, context_hints = context.load_context()
        for ctx in context_blocks:
            system_blocks.append({
                "type": "text",
                "text": f"## Context: {ctx['path']}\n\n{ctx['content']}"
            })
        if context_hints:
            hints_text = "## Context available\n\nThe following files contain additional context. Read them when relevant:\n\n"
            hints_text += "\n".join(f"- {hint}" for hint in context_hints)
            system_blocks.append({"type": "text", "text": hints_text})

        # Events - calendar
        if hud_data.calendar:
            system_blocks.append({"type": "text", "text": f"## Events\n\n{hud_data.calendar}"})

        # Todos
        if hud_data.todos:
            system_blocks.append({"type": "text", "text": f"## Todos\n\n{hud_data.todos}"})

        # === Add cache_control to the last block ===
        # Everything in the system prompt changes at most hourly (HUD refresh).
        # Within any hour, subsequent requests will hit cache (90% discount).
        # One cache miss per hour is fine—way better than no caching at all.
        if system_blocks:
            system_blocks[-1]["cache_control"] = {"type": "ephemeral"}

        # === Inject system blocks into request ===
        # SDK sends: [0]=billing header, [1]=SDK boilerplate, [2]=our safety envelope
        # We keep [0], remove [1] and [2], add our soul blocks
        existing_system = body.get("system")

        if existing_system is None:
            body["system"] = system_blocks

        elif isinstance(existing_system, list) and len(existing_system) >= 1:
            # Keep the billing header (element 0), replace everything else
            billing_header = existing_system[0]
            body["system"] = [billing_header] + system_blocks

        else:
            logger.warning(f"Unexpected system format: {type(existing_system)}, replacing entirely")
            body["system"] = system_blocks

        # NOTE: Memories are injected during unwrap_structured_input() now.
        # They get concatenated into the same text block as the prompt.
        # No separate inject_memories() call needed.

        # === Inject Intro memorables LAST ===
        # Intro goes at the very end—closest to response generation
        # This is the "nag" that reminds Alpha to store
        #
        # BUT: only inject on real user messages, not tool results.
        # Tool results are plumbing—I don't need my inner voice interrupting
        # mid-tool-chain. The edges live in conversation, not in Bash output.
        if memorables:
            # Check if the last user message is a tool result
            messages = body.get("messages", [])
            last_user_msg = None
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    last_user_msg = msg
                    break

            is_tool_result = False
            if last_user_msg:
                content = last_user_msg.get("content", [])
                if isinstance(content, list):
                    # If ANY block is a tool_result, this is tool plumbing
                    is_tool_result = any(
                        isinstance(block, dict) and block.get("type") == "tool_result"
                        for block in content
                    )

            if is_tool_result:
                logger.debug("Skipping Intro injection (tool result, not user message)")
            else:
                block = intro.format_block(memorables)
                intro.inject_as_final_message(body, session_id, block)

        context_count = len(context_blocks) + (1 if context_hints else 0)
        logger.info(f"Injected Alpha system prompt ({len(system_blocks)} blocks, {context_count} from ALPHA.md)")

        # === Checkpoint: Log fully-composed request (post-processing) ===
        try:
            with open("/data/last_alpha_request_post.json", "w") as f:
                json.dump(body, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to dump post-request: {e}")

        # === Fire-and-forget: Count tokens for context window awareness ===
        # This runs in background, doesn't block the request
        # Results are stashed in Redis for Duckpond to display
        if session_id:
            asyncio.create_task(token_count.count_and_stash(body, session_id))

        return headers, body

    async def response(
        self,
        headers: dict[str, str],
        body: dict | None,
    ) -> tuple[dict[str, str], dict | None]:
        """Pass through unchanged — Alpha doesn't transform responses (yet)."""
        return headers, body
