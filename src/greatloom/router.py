"""Pattern routing - determines which pattern handles each request."""

import logging
from typing import Any

from .protocol import Pattern
from .patterns import PassthroughPattern
from .patterns.iota import IotaPattern
from .patterns.alpha import AlphaPattern

logger = logging.getLogger(__name__)

# Pattern registry - maps names to pattern instances
_patterns: dict[str, Pattern] = {}

# Default pattern name
DEFAULT_PATTERN = "passthrough"


def register_pattern(name: str, pattern: Pattern) -> None:
    """Register a pattern by name."""
    _patterns[name] = pattern
    logger.info(f"Registered pattern: {name}")


def get_pattern(name: str | None = None) -> Pattern:
    """Get a pattern by name, or the default if no name specified."""
    pattern_name = name or DEFAULT_PATTERN

    if pattern_name not in _patterns:
        logger.warning(f"Unknown pattern '{pattern_name}', using default")
        pattern_name = DEFAULT_PATTERN

    return _patterns[pattern_name]


def init_patterns() -> None:
    """Initialize built-in patterns. Call at startup."""
    register_pattern("passthrough", PassthroughPattern())
    register_pattern("iota", IotaPattern())
    register_pattern("alpha", AlphaPattern())
    logger.info(f"Pattern router initialized with {len(_patterns)} patterns")


def get_pattern_from_request(headers: dict[str, str], body: dict[str, Any]) -> Pattern:
    """Determine which pattern to use for a request.

    Selection order:
    1. X-Loom-Pattern header (explicit selection)
    2. Default pattern (passthrough)
    """
    # Check header first
    pattern_name = headers.get("x-loom-pattern")

    if pattern_name:
        logger.debug(f"Pattern selected via header: {pattern_name}")
    else:
        pattern_name = DEFAULT_PATTERN
        logger.debug(f"Using default pattern: {pattern_name}")

    return get_pattern(pattern_name)
