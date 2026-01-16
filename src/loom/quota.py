"""
Quota tracking for the Loom.

Logs Anthropic API rate limit headers to Redis with automatic expiry.
Compatible with the Eavesdrop dashboard.
"""

import json
import logging
import os
from datetime import datetime, timezone

import redis

logger = logging.getLogger(__name__)

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://alpha-pi:6379")
_redis: redis.Redis | None = None

# TTL for quota entries (14 days)
TTL_DAYS = 14
TTL_SECONDS = TTL_DAYS * 24 * 60 * 60

# Quota headers we track
QUOTA_HEADERS = [
    "anthropic-ratelimit-unified-5h-utilization",
    "anthropic-ratelimit-unified-5h-reset",
    "anthropic-ratelimit-unified-5h-status",
    "anthropic-ratelimit-unified-7d-utilization",
    "anthropic-ratelimit-unified-7d-reset",
    "anthropic-ratelimit-unified-7d-status",
    "anthropic-ratelimit-unified-fallback",
    "anthropic-ratelimit-unified-fallback-percentage",
    "anthropic-ratelimit-unified-overage-status",
]


def get_redis() -> redis.Redis:
    """Get or create Redis connection."""
    global _redis
    if _redis is None:
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def log_quota(headers: dict):
    """Log quota headers to Redis with auto-expiry.

    Args:
        headers: Response headers dict from upstream
    """
    # Check if response has utilization headers
    util_5h = headers.get("anthropic-ratelimit-unified-5h-utilization")
    util_7d = headers.get("anthropic-ratelimit-unified-7d-utilization")
    if not util_5h and not util_7d:
        return

    timestamp = datetime.now(timezone.utc).isoformat()
    request_id = headers.get("request-id", "")

    data = {
        "timestamp": timestamp,
        "request_id": request_id,
        **{h: headers.get(h, "") for h in QUOTA_HEADERS},
    }

    # Key format: quota:<ISO timestamp> for natural sorting
    key = f"quota:{timestamp}"

    try:
        r = get_redis()
        r.setex(key, TTL_SECONDS, json.dumps(data))
    except redis.RedisError as e:
        logger.error(f"Redis error logging quota: {e}")
        return

    # Log current utilization
    logger.info(f"Quota: 5h={float(util_5h or 0)*100:.1f}%, 7d={float(util_7d or 0)*100:.1f}%")
