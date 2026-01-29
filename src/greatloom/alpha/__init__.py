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
import logging

import pendulum

from . import soul, hud, capsule, intro, compact, memories, token_count, scrub

logger = logging.getLogger(__name__)

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

        # === Phase 0: Check for auto-compact and rewrite if needed ===
        # This must happen FIRST, before we inject the normal system prompt
        body = compact.rewrite_auto_compact(body)

        # === Phase 0.5: Scrub noise from context ===
        # Remove known-bad blocks and substrings that add noise without value
        body = scrub.scrub_noise(body)

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
        existing_system = body.get("system")

        if existing_system is None:
            body["system"] = system_blocks

        elif isinstance(existing_system, list) and len(existing_system) >= 2:
            # Array format from Claude Agent SDK
            # Element 0 is SDK boilerplate — leave it alone
            # Replace element 1 and insert our additional blocks after it
            existing_system[1] = system_blocks[0]
            for i, block in enumerate(system_blocks[1:], start=2):
                existing_system.insert(i, block)
            body["system"] = existing_system

        elif isinstance(existing_system, list):
            existing_system.extend(system_blocks)
            body["system"] = existing_system

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

        # Dump the fully-composed request for debugging
        # Only Alpha pattern requests, not Haiku noise
        import json
        try:
            with open("/data/last_alpha_request.json", "w") as f:
                json.dump(body, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to dump request: {e}")

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
