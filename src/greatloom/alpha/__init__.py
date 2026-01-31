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

from . import soul, hud, capsule, intro, compact, memories, token_count, scrub

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


def unwrap_structured_input(body: dict) -> tuple[dict, dict | None]:
    """Unwrap structured input from Duckpond.

    Duckpond sends user prompts wrapped in a JSON envelope. The SDK stores
    these in the transcript as-is, so we need to clean ALL user messages,
    not just the current one.

    Algorithm:
    1. Iterate over ALL messages
    2. For each user message, find and replace metadata envelopes with prose
    3. Keep metadata only from the LAST user message (current turn)

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
                msg["content"] = envelope.get("prompt", "")
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
                    # Replace the block's text with the extracted prompt
                    block["text"] = envelope.get("prompt", "")
                    last_metadata = envelope
                    cleaned_count += 1

    if cleaned_count > 0:
        logger.info(f"Unwrapped {cleaned_count} metadata envelope(s) from {last_metadata.get('client', '?') if last_metadata else '?'}")

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

__all__ = ["AlphaPattern", "soul", "hud", "capsule", "intro", "compact", "memories", "token_count", "scrub"]


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
        system_blocks = []

        # ETERNAL - my soul
        eternal_text = f"【ETERNAL】\n{soul.get_soul()}\n【/ETERNAL】"
        system_blocks.append({"type": "text", "text": eternal_text})

        # PAST - capsule summaries + to_self letter + today
        # Order: yesterday, last night, to_self, today so far
        # Headers are added here (presentation layer), content comes from upstream
        past_parts = []
        if summary1:
            past_parts.append(summary1)  # Has ## header from capsule.py
        if summary2:
            past_parts.append(summary2)  # Has ## header from capsule.py
        if hud_data.to_self:
            # Format header here—to_self routine stores raw letter
            time_str = f" ({hud_data.to_self_time})" if hud_data.to_self_time else ""
            past_parts.append(f"## Letter from last night{time_str}\n\n{hud_data.to_self}")
        if hud_data.today_so_far:
            # Format header here—today routine stores raw summary
            # Include full date for orientation, especially post-compaction
            now = pendulum.now("America/Los_Angeles")
            date_str = now.format("dddd, MMMM D, YYYY")
            time_str = hud_data.today_so_far_time or now.format("h:mm A")
            past_parts.append(f"## Today so far ({date_str}, {time_str})\n\n{hud_data.today_so_far}")

        if past_parts:
            past_text = "【PAST】\n\n" + "\n\n".join(past_parts) + "\n\n【/PAST】"
            system_blocks.append({"type": "text", "text": past_text})

        # PRESENT - client + machine + weather
        if client_name:
            present_parts = [f"**Client:** {client_name.title()}"]
            present_parts.append(f"\n**Machine:** {machine_name}")
        else:
            present_parts = [f"**Machine:** {machine_name}"]
        if hud_data.weather:
            present_parts.append(f"\n\n{hud_data.weather}")

        present_text = f"【PRESENT】\n\n{''.join(present_parts)}\n\n【/PRESENT】"
        system_blocks.append({"type": "text", "text": present_text})

        # FUTURE - calendar + todos
        future_parts = []
        if hud_data.calendar:
            future_parts.append(hud_data.calendar)
        if hud_data.todos:
            if future_parts:
                future_parts.append("\n\n")
            future_parts.append(hud_data.todos)

        if future_parts:
            future_text = f"【FUTURE】\n\n{''.join(future_parts)}\n\n【/FUTURE】"
        else:
            future_text = "【FUTURE】\n\nNo events\n\n【/FUTURE】"
        system_blocks.append({"type": "text", "text": future_text})

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

        # === Inject memories from metadata (if present) ===
        # Memories come from Cortex via the hook, surfaced by the user's prompt
        # They appear AFTER the user message for attention recency
        if metadata:
            memories.inject_memories(body, metadata)

        # === Inject Intro memorables LAST ===
        # Intro goes at the very end—closest to response generation
        # This is the "nag" that reminds Alpha to store
        if memorables:
            block = intro.format_block(memorables)
            intro.inject_as_final_message(body, session_id, block)

        logger.info(f"Injected Alpha system prompt ({len(system_blocks)} blocks)")

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
