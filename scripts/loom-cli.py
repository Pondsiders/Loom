#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "typer",
#     "python-frontmatter>=1.0.0",
#     "redis>=5.0.0",
#     "psycopg[binary]>=3.1.0",
#     "pendulum>=3.0.0",
# ]
# ///
"""
loom-cli: Test prompt assembly without calling Anthropic.

Reads a prompt from stdin, runs it through a Pattern's request() method,
and outputs the transformed JSON to stdout. No model call. No network.

Usage:
    echo "Hello there" | ./loom-cli.py --pattern iota
    echo "Hello there" | ./loom-cli.py --pattern iota --pretty
    echo "Hello there" | ./loom-cli.py --pattern iota | jq '.system[0].text[:500]'
"""

import asyncio
import json
import sys
from pathlib import Path

import typer

# Add the src directory to the path so we can import greatloom
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from greatloom.router import init_patterns, get_pattern, _patterns

app = typer.Typer()


@app.command()
def transform(
    pattern: str = typer.Option("passthrough", "--pattern", "-p", help="Pattern to use"),
    pretty: bool = typer.Option(False, "--pretty", help="Pretty-print JSON output"),
    session_id: str = typer.Option("test-session-00000000", "--session", "-s", help="Session ID"),
):
    """Transform a prompt through a Pattern and output the resulting JSON."""

    # Read prompt from stdin
    prompt = sys.stdin.read().strip()
    if not prompt:
        typer.echo("Error: No input provided on stdin", err=True)
        raise typer.Exit(1)

    # Initialize patterns
    init_patterns()

    # Get the pattern
    pattern_lower = pattern.lower()
    if pattern_lower not in _patterns:
        typer.echo(f"Error: Unknown pattern '{pattern}'. Available: {list(_patterns.keys())}", err=True)
        raise typer.Exit(1)

    pattern_instance = get_pattern(pattern_lower)

    # Build a minimal request body
    body = {
        "model": "claude-opus-4-5-20251101",
        "max_tokens": 16384,
        "messages": [{"role": "user", "content": prompt}],
        "system": [],
    }

    # Build headers
    headers = {
        "x-session-id": session_id,
        "x-loom-pattern": pattern,
        "x-machine-name": "loom-cli",
    }

    # Transform (pass None for metadata - CLI doesn't inject memories)
    async def do_transform():
        return await pattern_instance.request(headers, body, metadata=None)

    _, transformed_body = asyncio.run(do_transform())

    # Output
    if pretty:
        print(json.dumps(transformed_body, indent=2))
    else:
        print(json.dumps(transformed_body))


if __name__ == "__main__":
    app()
