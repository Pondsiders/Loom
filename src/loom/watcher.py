"""Transcript watcher - tails JSONL files and publishes to Redis.

Watches Claude Code transcript files for changes, extracts new lines,
and publishes them to Redis pubsub for downstream consumers (Intro, Scribe).

The watcher is event-driven (inotify via watchfiles), not polling. It only
reads new bytes appended to the file, never re-parses from scratch.

Lifecycle:
- Watcher starts when the Loom sees a request for a session
- Each request resets the idle timeout (1 hour default)
- After idle timeout, watcher stops and cleans up
- If a new request comes in after timeout, watcher restarts from current EOF
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import redis.asyncio as aioredis
from watchfiles import awatch, Change

logger = logging.getLogger(__name__)

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://alpha-pi:6379")
_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Get or create async Redis connection."""
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis

# Default idle timeout: 60 seconds for testing (normally 1 hour)
IDLE_TIMEOUT_SECONDS = 60


@dataclass
class WatcherState:
    """State for an active transcript watcher."""

    task: asyncio.Task
    last_activity: float
    cancel_event: asyncio.Event
    session_id: str
    transcript_path: str
    file_pos: int = 0


# Global registry of active watchers, keyed by session_id
_watchers: dict[str, WatcherState] = {}


async def tail_file(filepath: Path, start_pos: int = 0) -> tuple[int, list[str]]:
    """Read new lines from a file starting at the given position.

    Args:
        filepath: Path to the file to read
        start_pos: Byte position to start reading from

    Returns:
        Tuple of (new_position, list_of_new_lines)
    """
    try:
        async with asyncio.timeout(5):  # Don't hang forever on file ops
            # Use sync file ops wrapped in executor - simpler than aiofile for this
            def read_new_content():
                with open(filepath, "r") as f:
                    f.seek(start_pos)
                    content = f.read()
                    new_pos = f.tell()
                    return new_pos, content

            loop = asyncio.get_event_loop()
            new_pos, content = await loop.run_in_executor(None, read_new_content)

            if content:
                lines = [line for line in content.strip().split("\n") if line]
                return new_pos, lines
            return new_pos, []

    except Exception as e:
        logger.error(f"Error reading {filepath}: {e}")
        return start_pos, []


def classify_line(line: str) -> dict | None:
    """Parse a JSONL line and extract the interesting bits.

    Returns a dict with:
        - type: The message type (user, assistant, system, etc.)
        - role: The message role if present
        - content_types: List of content block types (text, tool_use, tool_result)
        - raw: The original JSON (for logging)

    Returns None if the line can't be parsed.
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    msg_type = data.get("type")
    message = data.get("message", {})
    role = message.get("role")

    # Extract content block types
    content = message.get("content", [])
    if isinstance(content, str):
        content_types = ["text"]
    elif isinstance(content, list):
        content_types = list({block.get("type", "text") for block in content if isinstance(block, dict)})
    else:
        content_types = []

    return {
        "type": msg_type,
        "role": role,
        "content_types": content_types,
        "raw": data,
    }


async def run_watcher(
    session_id: str,
    transcript_path: str,
    cancel_event: asyncio.Event,
    redis_client=None,  # Optional, for future Redis publishing
):
    """Watch a transcript file and log/publish new lines.

    Args:
        session_id: The session ID this transcript belongs to
        transcript_path: Path to the JSONL transcript file
        cancel_event: Event to signal cancellation
        redis_client: Optional Redis client for publishing (not used yet)
    """
    path = Path(transcript_path)

    if not path.exists():
        logger.warning(f"Transcript file does not exist: {transcript_path}")
        return

    # Start from current end of file (we only want NEW content)
    # The state should exist by now (ensure_watcher creates it before spawning this task)
    # but we initialize to EOF as a fallback
    file_pos = path.stat().st_size
    if session_id in _watchers:
        _watchers[session_id].file_pos = file_pos

    logger.info(f"Watcher starting: session={session_id[:8]}, path={path.name}, pos={file_pos}")

    try:
        # Use polling instead of inotify - inotify doesn't work reliably across Docker volume mounts
        async for changes in awatch(path, stop_event=cancel_event, poll_delay_ms=100):
            # Check idle timeout
            if session_id in _watchers:
                state = _watchers[session_id]
                if time.time() - state.last_activity > IDLE_TIMEOUT_SECONDS:
                    logger.info(f"Watcher idle timeout: session={session_id[:8]}")
                    break

            # Process file changes
            for change_type, change_path in changes:
                if change_type != Change.modified:
                    continue

                # Read new content
                new_pos, new_lines = await tail_file(path, file_pos)

                if new_lines:
                    logger.info(f"Watcher: {len(new_lines)} new lines in session={session_id[:8]}")

                    for line in new_lines:
                        classified = classify_line(line)
                        if classified:
                            # Log the classified line (this goes to Logfire via OTel)
                            logger.info(
                                f"Transcript line: session={session_id[:8]} "
                                f"type={classified['type']} "
                                f"role={classified['role']} "
                                f"content={','.join(classified['content_types'])}"
                            )

                            # Log the full JSON for detailed inspection
                            logger.debug(f"Transcript raw: {json.dumps(classified['raw'])}")

                            # Publish to Redis pubsub
                            try:
                                r = await get_redis()
                                channel = f"transcript:{session_id}"
                                payload = json.dumps({
                                    "session_id": session_id,
                                    "type": classified["type"],
                                    "role": classified["role"],
                                    "content_types": classified["content_types"],
                                    "raw": classified["raw"],
                                })
                                await r.publish(channel, payload)
                                logger.debug(f"Published to {channel}")
                            except Exception as e:
                                logger.error(f"Redis publish error: {e}")

                # Update position
                file_pos = new_pos
                if session_id in _watchers:
                    _watchers[session_id].file_pos = new_pos

    except asyncio.CancelledError:
        logger.info(f"Watcher cancelled: session={session_id[:8]}")
    except Exception as e:
        logger.error(f"Watcher error: session={session_id[:8]}, error={e}")
    finally:
        # Clean up
        if session_id in _watchers:
            del _watchers[session_id]
        logger.info(f"Watcher stopped: session={session_id[:8]}")


async def ensure_watcher(session_id: str, transcript_path: str) -> None:
    """Ensure a watcher is running for this session, reset idle timeout.

    This is the main entry point. Call this on every API request.

    Args:
        session_id: The session ID
        transcript_path: Path to the transcript file to watch
    """
    now = time.time()

    if session_id in _watchers:
        # Already watching - just reset the timer
        _watchers[session_id].last_activity = now
        logger.info(f"Watcher refreshed: session={session_id[:8]}")
        return

    # Start a new watcher
    cancel_event = asyncio.Event()
    task = asyncio.create_task(
        run_watcher(session_id, transcript_path, cancel_event)
    )

    _watchers[session_id] = WatcherState(
        task=task,
        last_activity=now,
        cancel_event=cancel_event,
        session_id=session_id,
        transcript_path=transcript_path,
        file_pos=0,  # Will be set properly in run_watcher
    )

    logger.info(f"Watcher started: session={session_id[:8]}, path={transcript_path}")


async def stop_watcher(session_id: str) -> None:
    """Stop a watcher for a session.

    Args:
        session_id: The session ID to stop watching
    """
    if session_id not in _watchers:
        return

    state = _watchers[session_id]
    state.cancel_event.set()
    state.task.cancel()

    try:
        await state.task
    except asyncio.CancelledError:
        pass

    logger.info(f"Watcher stopped manually: session={session_id[:8]}")


def get_active_watchers() -> list[str]:
    """Return list of session IDs with active watchers."""
    return list(_watchers.keys())
