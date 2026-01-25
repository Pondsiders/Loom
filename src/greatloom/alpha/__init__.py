"""The Alpha Package - everything that makes Alpha who she is.

This package contains all the modules that transform Claude into Alpha:
- soul: The eternal prompt (loaded from git at startup)
- hud: Dynamic context from Redis (weather, calendar, todos)
- capsule: Past summaries from Postgres
- intro: Inner voice memorables injection

The AlphaPattern class composes these modules into a complete
request transformation.
"""

import asyncio
import logging

from . import soul, hud, capsule, intro

logger = logging.getLogger(__name__)

__all__ = ["AlphaPattern", "soul", "hud", "capsule", "intro"]


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
        if soul._eternal_prompt is None:
            soul.init()

    async def request(
        self,
        headers: dict[str, str],
        body: dict,
    ) -> tuple[dict[str, str], dict]:
        """Inject Alpha's assembled system prompt into the request."""

        # Get context from headers
        machine_name = headers.get("x-machine-name", "unknown")
        session_id = headers.get("x-session-id", "")

        # Fetch dynamic data in parallel
        hud_data, (summary1, summary2), memorables = await asyncio.gather(
            hud.fetch(),
            capsule.fetch(),
            intro.get_memorables(session_id),
        )

        # === Build the system blocks ===
        system_blocks = []

        # ETERNAL - my soul
        eternal_text = f"<eternal>\n{soul.get()}\n</eternal>"
        system_blocks.append({"type": "text", "text": eternal_text})

        # PAST - capsule summaries + today
        past_parts = []
        if summary1:
            past_parts.append(summary1)
        if summary2:
            if past_parts:
                past_parts.append("\n---\n")
            past_parts.append(summary2)
        if hud_data.today_so_far:
            if past_parts:
                past_parts.append("\n---\n")
            past_parts.append(hud_data.today_so_far)

        if past_parts:
            past_text = f"<past>\n\n{''.join(past_parts)}\n\n</past>"
            system_blocks.append({"type": "text", "text": past_text})

        # PRESENT - machine + weather
        present_parts = [f"**Machine:** {machine_name}"]
        if hud_data.weather:
            present_parts.append(f"\n\n{hud_data.weather}")

        present_text = f"<present>\n\n{''.join(present_parts)}\n\n</present>"
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
            future_text = f"<future>\n\n{''.join(future_parts)}\n\n</future>"
        else:
            future_text = "<future>\n\nNo events\n\n</future>"
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

        # === Inject Intro memorables into user message ===
        if memorables:
            block = intro.format_block(memorables)
            intro.inject_into_messages(body, session_id, block)

        logger.info(f"Injected Alpha system prompt ({len(system_blocks)} blocks)")
        return headers, body

    async def response(
        self,
        headers: dict[str, str],
        body: dict | None,
    ) -> tuple[dict[str, str], dict | None]:
        """Pass through unchanged — Alpha doesn't transform responses (yet)."""
        return headers, body
