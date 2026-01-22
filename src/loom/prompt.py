"""Dynamic system prompt assembly.

Fetches Alpha's soul from GitHub (or fallback to disk), combines with
dynamic context from Redis, renders through Jinja2 template.

Architecture:
- ETERNAL: Fetched once at startup from GitHub, cached forever
- PAST: Capsule summaries from Postgres (fetched per-request)
- PRESENT: Machine info (from request metadata) + weather (from Redis)
- FUTURE: Calendar + todos (from Redis)

On startup:
1. Try GitHub raw URL (with auth if GITHUB_TOKEN set)
2. If fail, read from FALLBACK_PROMPT_PATH
3. If both fail, panic

Per request:
1. Use cached eternal
2. Fetch dynamic content from Redis
3. Render template
4. Inject into system[1]
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import psycopg
import redis
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

# === Configuration ===

# GitHub source (primary)
GITHUB_REPO = os.environ.get("PROMPT_GITHUB_REPO", "alphafornow/alpha-system-prompt")
GITHUB_BRANCH = os.environ.get("PROMPT_GITHUB_BRANCH", "main")
GITHUB_FILE = os.environ.get("PROMPT_GITHUB_FILE", "system-prompt.md")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# Disk fallback (secondary)
FALLBACK_PROMPT_PATH = Path(os.environ.get(
    "FALLBACK_PROMPT_PATH",
    "/Pondside/Alpha-Home/self/system-prompt/system-prompt.md"
))

# Database for Capsule summaries
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Redis for HUD data
REDIS_URL = os.environ.get("REDIS_URL", "redis://alpha-pi:6379")

# Template directory
TEMPLATES_DIR = Path(__file__).parent.parent.parent / "prompts"


# === Cached State ===

_eternal_prompt: str | None = None


# === Data Classes ===

@dataclass
class CapsuleSummary:
    """A Capsule summary with its time period."""
    period_start: str
    period_end: str
    summary: str

    def format_header(self) -> str:
        """Format a human-readable header for this summary period."""
        import pendulum

        start = pendulum.parse(self.period_start).in_timezone("America/Los_Angeles")
        end = pendulum.parse(self.period_end).in_timezone("America/Los_Angeles")

        is_night = start.hour >= 22 or start.hour < 6

        if is_night:
            day_name = start.format("dddd")
            month = start.format("MMM")
            start_day = start.day
            end_day = end.day
            year = end.year
            return f"{day_name} night {month} {start_day}-{end_day} {year}"
        else:
            return start.format("dddd MMM D YYYY")


# === Startup: Fetch Eternal Prompt ===

def _fetch_from_github() -> str | None:
    """Fetch system prompt from GitHub. Returns None on failure."""
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FILE}"
    headers = {}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    try:
        # Synchronous fetch at startup is fine
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            logger.info(f"Fetched eternal prompt from GitHub ({len(response.text)} chars)")
            return response.text
    except Exception as e:
        logger.warning(f"Failed to fetch from GitHub: {e}")
        return None


def _fetch_from_disk() -> str | None:
    """Fetch system prompt from disk fallback. Returns None on failure."""
    try:
        if FALLBACK_PROMPT_PATH.exists():
            text = FALLBACK_PROMPT_PATH.read_text()
            logger.info(f"Fetched eternal prompt from disk ({len(text)} chars)")
            return text
        else:
            logger.warning(f"Fallback path does not exist: {FALLBACK_PROMPT_PATH}")
            return None
    except Exception as e:
        logger.warning(f"Failed to read from disk: {e}")
        return None


def init_eternal_prompt() -> None:
    """Initialize the eternal prompt at startup.

    Tries GitHub first, then disk fallback. Panics if both fail.
    Call this once during application startup.
    """
    global _eternal_prompt

    logger.info("Initializing eternal prompt...")
    logger.info(f"  GitHub: {GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FILE}")
    logger.info(f"  Fallback: {FALLBACK_PROMPT_PATH}")

    # Try GitHub first
    _eternal_prompt = _fetch_from_github()

    # Fall back to disk
    if _eternal_prompt is None:
        logger.info("GitHub unavailable, trying disk fallback...")
        _eternal_prompt = _fetch_from_disk()

    # Panic if both fail
    if _eternal_prompt is None:
        raise RuntimeError(
            f"FATAL: Could not load eternal prompt from GitHub or disk. "
            f"GitHub: {GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FILE}, "
            f"Disk: {FALLBACK_PROMPT_PATH}"
        )

    logger.info(f"Eternal prompt loaded ({len(_eternal_prompt)} chars)")


def get_eternal_prompt() -> str:
    """Get the cached eternal prompt. Must call init_eternal_prompt() first."""
    if _eternal_prompt is None:
        raise RuntimeError("Eternal prompt not initialized. Call init_eternal_prompt() first.")
    return _eternal_prompt


# === Per-Request: Fetch Dynamic Content ===

def _get_redis() -> redis.Redis:
    """Get Redis connection."""
    return redis.from_url(REDIS_URL, decode_responses=True)


def _get_redis_value(r: redis.Redis, key: str) -> str | None:
    """Get a string value from Redis, or None if not found."""
    try:
        return r.get(key)
    except Exception as e:
        logger.warning(f"Redis error fetching {key}: {e}")
        return None


def get_capsule_summaries() -> tuple[CapsuleSummary | None, CapsuleSummary | None]:
    """Get the two most recent Capsule summaries from Postgres.

    Returns (summary1, summary2) where:
    - summary1 is the second most recent (X-2)
    - summary2 is the most recent completed (X-1)
    """
    if not DATABASE_URL:
        logger.debug("No DATABASE_URL, skipping Capsule summaries")
        return None, None

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT period_start, period_end, summary
                    FROM cortex.summaries
                    ORDER BY period_start DESC
                    LIMIT 2
                """)
                rows = cur.fetchall()

                summaries = [
                    CapsuleSummary(
                        period_start=row[0].isoformat() if row[0] else "",
                        period_end=row[1].isoformat() if row[1] else "",
                        summary=row[2],
                    )
                    for row in rows
                ]

                if len(summaries) >= 2:
                    return summaries[1], summaries[0]
                elif len(summaries) == 1:
                    return None, summaries[0]
                else:
                    return None, None
    except Exception as e:
        logger.warning(f"Error fetching Capsule summaries: {e}")
        return None, None


def get_hud_data() -> dict[str, str | None]:
    """Fetch HUD data from Redis.

    Returns dict with keys: weather, calendar, todos, today_so_far
    """
    try:
        r = _get_redis()
        return {
            "weather": _get_redis_value(r, "hud:weather"),
            "calendar": _get_redis_value(r, "hud:calendar"),
            "todos": _get_redis_value(r, "hud:todos"),
            "today_so_far": _get_redis_value(r, "systemprompt:past:today"),
        }
    except Exception as e:
        logger.warning(f"Error fetching HUD data: {e}")
        return {"weather": None, "calendar": None, "todos": None, "today_so_far": None}


# === Template Rendering ===

def build_system_prompt(machine_name: str | None = None) -> str:
    """Build the complete system prompt from all sources.

    Args:
        machine_name: Name of the machine making the request (from metadata).
                     Used in <present> section.

    Returns:
        The fully assembled system prompt.
    """
    # Load template
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template("system_prompt.jinja2")

    # Gather data
    eternal = get_eternal_prompt()
    summary1, summary2 = get_capsule_summaries()
    hud = get_hud_data()

    # Render template
    return template.render(
        eternal=eternal,
        summary1=summary1,
        summary2=summary2,
        machine_name=machine_name,
        weather=hud["weather"],
        calendar=hud["calendar"],
        todos=hud["todos"],
        today_so_far=hud["today_so_far"],
    )


# === System Prompt Injection ===

def inject_system_prompt(body: dict[str, Any], machine_name: str | None = None) -> dict[str, Any]:
    """Inject the assembled system prompt into the request body.

    Replaces system[1] (the client's system prompt) with our assembled version.
    Preserves system[0] (the SDK preamble).

    Args:
        body: The request body dict
        machine_name: Name of the machine (for <present> section)

    Returns:
        Modified request body with assembled system prompt
    """
    system = body.get("system")

    # Only process if system is a list with at least 2 items
    if not isinstance(system, list) or len(system) < 2:
        logger.debug("System prompt not in expected format, skipping injection")
        return body

    # Build the assembled prompt
    assembled = build_system_prompt(machine_name=machine_name)

    # Replace system[1]
    if isinstance(system[1], dict) and system[1].get("type") == "text":
        system[1]["text"] = assembled
        logger.info(f"Injected assembled system prompt ({len(assembled)} chars)")
    elif isinstance(system[1], str):
        system[1] = assembled
        logger.info(f"Injected assembled system prompt ({len(assembled)} chars)")
    else:
        logger.warning(f"Unexpected system[1] format: {type(system[1])}")

    return body
