"""HUD - the heads-up display data from Redis.

Present: weather
Future: calendar, todos
Past (today): running summary of today's events

All fetched from Redis keys populated by Pulse.
"""

import asyncio
import logging
import os
from dataclasses import dataclass

import redis.asyncio as redis

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://alpha-pi:6379")


@dataclass
class HUDData:
    """Container for HUD data."""
    weather: str | None = None
    calendar: str | None = None
    todos: str | None = None
    to_self: str | None = None  # Nightly letter from last-night-me
    to_self_time: str | None = None  # Timestamp for header formatting
    today_so_far: str | None = None  # Rolling summary of today
    today_so_far_time: str | None = None  # Timestamp for header formatting


async def _get_redis() -> redis.Redis:
    """Get async Redis connection."""
    return redis.from_url(REDIS_URL, decode_responses=True)


async def fetch() -> HUDData:
    """Fetch HUD data from Redis.

    All fetches happen in parallel for speed.
    Returns HUDData with None for any failed/missing values.
    """
    try:
        r = await _get_redis()

        # Parallel fetches
        weather, calendar, todos, to_self, to_self_time, today_so_far, today_so_far_time = await asyncio.gather(
            r.get("hud:weather"),
            r.get("hud:calendar"),
            r.get("hud:todos"),
            r.get("systemprompt:past:to_self"),
            r.get("systemprompt:past:to_self:time"),
            r.get("systemprompt:past:today"),
            r.get("systemprompt:past:today:time"),
            return_exceptions=True,
        )

        await r.aclose()

        # Convert exceptions to None
        return HUDData(
            weather=weather if not isinstance(weather, Exception) else None,
            calendar=calendar if not isinstance(calendar, Exception) else None,
            todos=todos if not isinstance(todos, Exception) else None,
            to_self=to_self if not isinstance(to_self, Exception) else None,
            to_self_time=to_self_time if not isinstance(to_self_time, Exception) else None,
            today_so_far=today_so_far if not isinstance(today_so_far, Exception) else None,
            today_so_far_time=today_so_far_time if not isinstance(today_so_far_time, Exception) else None,
        )
    except Exception as e:
        logger.warning(f"Error fetching HUD data: {e}")
        return HUDData()
