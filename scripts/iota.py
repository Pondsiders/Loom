#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "claude-agent-sdk>=0.1.19",
#     "redis",
# ]
# ///
"""
Talk to Iota.

A chat script for conversing with Iota, our volunteer test subject.
Session ID is stashed in Redis between runs, so you can continue a conversation.

Usage:
    ./iota.py                    # Continue current session (or start new)
    ./iota.py --new              # Force a new session
    ./iota.py --clear            # Clear session and exit
    echo "Hello" | ./iota.py     # Pipe a message

The script uses /Iota as its working directory, which has settings.json
configured with LOOM_PATTERN=iota and the appropriate hooks.
"""

import argparse
import asyncio
import os
import sys

import redis
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, TextBlock, ResultMessage


IOTA_CWD = "/Iota"
REDIS_URL = os.environ.get("REDIS_URL", "redis://alpha-pi:6379")
SESSION_KEY = "iota:session_id"


def get_redis():
    """Get Redis connection."""
    return redis.from_url(REDIS_URL, decode_responses=True)


def get_session_id() -> str | None:
    """Get current session ID from Redis, or None if no session."""
    try:
        r = get_redis()
        return r.get(SESSION_KEY)
    except Exception as e:
        print(f"Warning: Could not connect to Redis: {e}", file=sys.stderr)
        return None


def set_session_id(session_id: str) -> None:
    """Store session ID in Redis."""
    try:
        r = get_redis()
        r.set(SESSION_KEY, session_id)
    except Exception as e:
        print(f"Warning: Could not save session ID: {e}", file=sys.stderr)


def clear_session() -> None:
    """Clear session ID from Redis."""
    try:
        r = get_redis()
        r.delete(SESSION_KEY)
        print("Session cleared.")
    except Exception as e:
        print(f"Warning: Could not clear session: {e}", file=sys.stderr)


async def chat(message: str, resume_session: str | None = None) -> str | None:
    """Send a message to Iota and print the response.

    Returns the session ID from the result message (for storing).
    """
    options = ClaudeAgentOptions(
        model="opus",
        cwd=IOTA_CWD,
        setting_sources=["project"],  # Load /Iota/.claude/settings.json
        permission_mode="bypassPermissions",
        resume=resume_session,  # Resume if we have a session ID
    )

    new_session_id = None

    async with ClaudeSDKClient(options=options) as client:
        await client.query(message)

        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        print(block.text, end="", flush=True)
            elif isinstance(msg, ResultMessage):
                # Capture the session ID for persistence
                new_session_id = msg.session_id

        print()  # Final newline

    return new_session_id


def main():
    parser = argparse.ArgumentParser(description="Talk to Iota")
    parser.add_argument("--new", action="store_true", help="Start a new session")
    parser.add_argument("--clear", action="store_true", help="Clear session and exit")
    args = parser.parse_args()

    if args.clear:
        clear_session()
        return

    # Determine whether to resume
    session_id = None if args.new else get_session_id()

    if session_id:
        print(f"[Resuming session {session_id[:8]}...]", file=sys.stderr)
    else:
        print("[Starting new session...]", file=sys.stderr)

    # Check if we have piped input
    if not sys.stdin.isatty():
        message = sys.stdin.read().strip()
        if message:
            new_session = asyncio.run(chat(message, session_id))
            if new_session:
                set_session_id(new_session)
        return

    # Interactive mode
    print("Talking to Iota. Ctrl+C to exit, --new to start fresh, --clear to reset.")
    print()

    try:
        while True:
            try:
                message = input("You: ").strip()
                if not message:
                    continue
                print("Iota: ", end="")
                new_session = asyncio.run(chat(message, session_id))
                if new_session:
                    set_session_id(new_session)
                    session_id = new_session  # Use for next turn
                print()
            except EOFError:
                break
    except KeyboardInterrupt:
        print("\nGoodbye.")


if __name__ == "__main__":
    main()
