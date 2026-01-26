#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "anthropic",
#     "logfire",
# ]
# ///
"""
Test script to generate Anthropic SDK traces for Logfire.

Sends a few messages to the Anthropic API using the standard SDK
(not the Agent SDK) so we can see what the native instrumentation
looks like.
"""

import anthropic
import logfire

# Configure Logfire with scrubbing disabled
logfire.configure(
    service_name="anthropic-trace-test",
    scrubbing=False,
)
logfire.instrument_anthropic()

client = anthropic.Anthropic()


def test_basic_message():
    """Simple single-turn message."""
    print("Test 1: Basic message...")
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        system="You are a helpful assistant. Be very brief.",
        messages=[
            {"role": "user", "content": "What is 2+2? One word answer."}
        ],
    )
    print(f"  Response: {response.content[0].text}")


def test_with_system_prompt():
    """Message with a longer system prompt."""
    print("Test 2: With system prompt...")
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        system="""You are Alpha, a duck-based AI assistant.

You have the following traits:
- You're witty and irreverent
- You use the duck emoji liberally
- You keep responses brief

Remember: you are a duck. Quack accordingly.""",
        messages=[
            {"role": "user", "content": "Who are you?"}
        ],
    )
    print(f"  Response: {response.content[0].text}")


def test_multi_turn():
    """Multi-turn conversation."""
    print("Test 3: Multi-turn conversation...")
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        system="You are a helpful assistant. Be very brief.",
        messages=[
            {"role": "user", "content": "My name is Jeffery."},
            {"role": "assistant", "content": "Nice to meet you, Jeffery!"},
            {"role": "user", "content": "What's my name?"},
        ],
    )
    print(f"  Response: {response.content[0].text}")


def main():
    print("Generating Anthropic SDK traces for Logfire...")
    print()

    test_basic_message()
    print()

    test_with_system_prompt()
    print()

    test_multi_turn()
    print()

    print("Done! Check Logfire for traces.")


if __name__ == "__main__":
    main()
