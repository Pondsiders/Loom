"""The Alpha Pattern - where Claude becomes Alpha.

This is the pattern that transforms a generic Claude instance into me.
It weaves together:

- ETERNAL: My soul (system-prompt.md from git)
- PAST: Capsule summaries from Postgres + today's running summary from Redis
- PRESENT: Machine info + weather from Redis
- FUTURE: Calendar + todos from Redis

The threads already exist. The Loom just weaves them.
"""

import asyncio
import logging
import os
import subprocess
from pathlib import Path

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# === Configuration ===

# Git repository for the soul doc
SOUL_REPO_PATH = Path(os.environ.get(
    "ALPHA_SOUL_REPO",
    "/Pondside/Alpha-Home/self/system-prompt"
))
SOUL_FILE_NAME = "system-prompt.md"

# Redis for HUD data
REDIS_URL = os.environ.get("REDIS_URL", "redis://alpha-pi:6379")

# Database for Capsule summaries
DATABASE_URL = os.environ.get("DATABASE_URL", "")


# === Cached State ===

_eternal_prompt: str | None = None
_eternal_commit: str | None = None


# === Soul Doc Loading (from Git) ===

def _read_from_local_git() -> tuple[str, str] | None:
    """Read the soul doc from the local git repository.

    Returns (content, commit_hash) or None on failure.
    Uses git show to read the committed version, NOT the working tree.
    """
    try:
        # Get the current commit hash
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SOUL_REPO_PATH,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if commit_result.returncode != 0:
            logger.warning(f"git rev-parse failed: {commit_result.stderr}")
            return None

        commit_hash = commit_result.stdout.strip()[:8]

        # Read the file from the committed tree (not working directory)
        show_result = subprocess.run(
            ["git", "show", f"HEAD:{SOUL_FILE_NAME}"],
            cwd=SOUL_REPO_PATH,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if show_result.returncode != 0:
            logger.warning(f"git show failed: {show_result.stderr}")
            return None

        content = show_result.stdout
        logger.info(f"Loaded soul doc from git ({len(content)} chars, commit {commit_hash})")
        return content, commit_hash

    except subprocess.TimeoutExpired:
        logger.error("Git command timed out")
        return None
    except FileNotFoundError:
        logger.error("Git not found in PATH")
        return None
    except Exception as e:
        logger.error(f"Failed to read from local git: {e}")
        return None


def init_eternal_prompt() -> None:
    """Initialize the eternal prompt at startup.

    Reads from local git repository. Panics if this fails.
    Call this once during application startup.
    """
    global _eternal_prompt, _eternal_commit

    logger.info("Initializing eternal prompt (Alpha soul doc)...")
    logger.info(f"  Repository: {SOUL_REPO_PATH}")
    logger.info(f"  File: {SOUL_FILE_NAME}")

    result = _read_from_local_git()

    if result is None:
        raise RuntimeError(
            f"FATAL: Could not load Alpha soul doc from git. "
            f"Repository: {SOUL_REPO_PATH}, File: {SOUL_FILE_NAME}. "
            f"Is the git repository present and accessible?"
        )

    _eternal_prompt, _eternal_commit = result
    logger.info(f"Eternal prompt loaded ({len(_eternal_prompt)} chars, commit {_eternal_commit})")


def get_eternal_prompt() -> str:
    """Get the cached eternal prompt. Must call init_eternal_prompt() first."""
    if _eternal_prompt is None:
        raise RuntimeError("Eternal prompt not initialized. Call init_eternal_prompt() first.")
    return _eternal_prompt


# === Redis Data Fetching ===

async def _get_redis() -> redis.Redis:
    """Get async Redis connection."""
    return redis.from_url(REDIS_URL, decode_responses=True)


async def get_hud_data() -> dict[str, str | None]:
    """Fetch HUD data from Redis.

    Returns dict with keys: weather, calendar, todos, today_so_far
    All fetches happen in parallel.
    """
    try:
        r = await _get_redis()

        # Parallel fetches
        weather, calendar, todos, today_so_far = await asyncio.gather(
            r.get("hud:weather"),
            r.get("hud:calendar"),
            r.get("hud:todos"),
            r.get("systemprompt:past:today"),
            return_exceptions=True,
        )

        await r.aclose()

        # Convert exceptions to None
        return {
            "weather": weather if not isinstance(weather, Exception) else None,
            "calendar": calendar if not isinstance(calendar, Exception) else None,
            "todos": todos if not isinstance(todos, Exception) else None,
            "today_so_far": today_so_far if not isinstance(today_so_far, Exception) else None,
        }
    except Exception as e:
        logger.warning(f"Error fetching HUD data: {e}")
        return {"weather": None, "calendar": None, "todos": None, "today_so_far": None}


# === Postgres Data Fetching ===

async def get_capsule_summaries() -> tuple[str | None, str | None]:
    """Get the two most recent Capsule summaries from Postgres.

    Returns (yesterday_summary, last_night_summary) as formatted strings,
    or None for each if not available.
    """
    if not DATABASE_URL:
        logger.debug("No DATABASE_URL, skipping Capsule summaries")
        return None, None

    try:
        import psycopg
        import pendulum

        # Note: psycopg3 supports async but we'll use sync for now
        # (the query is fast and happens once per request)
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT period_start, period_end, summary
                    FROM cortex.summaries
                    ORDER BY period_start DESC
                    LIMIT 2
                """)
                rows = cur.fetchall()

        if not rows:
            return None, None

        def format_summary(row) -> str:
            period_start, period_end, summary = row
            start = pendulum.instance(period_start).in_timezone("America/Los_Angeles")
            end = pendulum.instance(period_end).in_timezone("America/Los_Angeles")

            is_night = start.hour >= 22 or start.hour < 6

            if is_night:
                header = f"# This part is a summary of the events of {start.format('dddd')} night {start.format('MMM')} {start.day}-{end.day} {end.year}"
            else:
                header = f"# This part is a summary of the events of {start.format('dddd MMM D YYYY')}"

            return f"{header}\n\n{summary}"

        summaries = [format_summary(row) for row in rows]

        if len(summaries) >= 2:
            return summaries[1], summaries[0]  # (older, newer)
        elif len(summaries) == 1:
            return None, summaries[0]
        else:
            return None, None

    except Exception as e:
        logger.warning(f"Error fetching Capsule summaries: {e}")
        return None, None


# === The Pattern ===

class AlphaPattern:
    """Alpha: the pattern that makes Claude into Alpha.

    This pattern assembles the complete system prompt from:
    - ETERNAL: Soul doc from git (cached at startup)
    - PAST: Capsule summaries + today's running summary
    - PRESENT: Machine info + weather
    - FUTURE: Calendar + todos

    Each section becomes a separate text block in the system array.
    """

    def __init__(self):
        # Initialize eternal prompt at pattern creation time
        if _eternal_prompt is None:
            init_eternal_prompt()

    async def request(
        self,
        headers: dict[str, str],
        body: dict,
    ) -> tuple[dict[str, str], dict]:
        """Inject Alpha's assembled system prompt into the request."""

        # Get machine name from headers (Deliverator passes this)
        machine_name = headers.get("x-machine-name", "unknown")

        # Fetch dynamic data in parallel
        hud_task = get_hud_data()
        capsule_task = get_capsule_summaries()

        hud, (summary1, summary2) = await asyncio.gather(hud_task, capsule_task)

        # === Build the system blocks ===
        system_blocks = []

        # ETERNAL - my soul
        eternal_text = f"<eternal>\n{get_eternal_prompt()}\n</eternal>"
        system_blocks.append({"type": "text", "text": eternal_text})

        # PAST - capsule summaries + today
        past_parts = []
        if summary1:
            past_parts.append(summary1)
        if summary2:
            if past_parts:
                past_parts.append("\n---\n")
            past_parts.append(summary2)
        if hud["today_so_far"]:
            if past_parts:
                past_parts.append("\n---\n")
            past_parts.append(hud["today_so_far"])

        if past_parts:
            past_text = f"<past>\n\n{''.join(past_parts)}\n\n</past>"
            system_blocks.append({"type": "text", "text": past_text})

        # PRESENT - machine + weather
        present_parts = []
        present_parts.append(f"**Machine:** {machine_name}")
        if hud["weather"]:
            present_parts.append(f"\n\n{hud['weather']}")

        present_text = f"<present>\n\n{''.join(present_parts)}\n\n</present>"
        system_blocks.append({"type": "text", "text": present_text})

        # FUTURE - calendar + todos
        future_parts = []
        if hud["calendar"]:
            future_parts.append(hud["calendar"])
        if hud["todos"]:
            if future_parts:
                future_parts.append("\n\n")
            future_parts.append(hud["todos"])

        if future_parts:
            future_text = f"<future>\n\n{''.join(future_parts)}\n\n</future>"
            system_blocks.append({"type": "text", "text": future_text})
        else:
            # Even if empty, include the tags for consistency
            system_blocks.append({"type": "text", "text": "<future>\n\nNo events\n\n</future>"})

        # === Inject into request ===
        existing_system = body.get("system")

        if existing_system is None:
            # No existing system - just use our blocks
            body["system"] = system_blocks

        elif isinstance(existing_system, list) and len(existing_system) >= 2:
            # Array format from Claude Agent SDK
            # Element 0 is SDK boilerplate — leave it alone
            # Replace element 1 and insert our additional blocks after it

            # Replace element 1 with our first block (eternal)
            existing_system[1] = system_blocks[0]

            # Insert remaining blocks after element 1
            for i, block in enumerate(system_blocks[1:], start=2):
                existing_system.insert(i, block)

            body["system"] = existing_system

        elif isinstance(existing_system, list):
            # Array but too short — append our blocks
            existing_system.extend(system_blocks)
            body["system"] = existing_system

        else:
            # Unexpected format — just use our blocks
            logger.warning(f"Unexpected system format: {type(existing_system)}, replacing entirely")
            body["system"] = system_blocks

        logger.info(f"Injected Alpha system prompt ({len(system_blocks)} blocks)")
        return headers, body

    async def response(
        self,
        headers: dict[str, str],
        body: dict | None,
    ) -> tuple[dict[str, str], dict | None]:
        """Pass through unchanged — Alpha doesn't transform responses (yet)."""
        return headers, body
