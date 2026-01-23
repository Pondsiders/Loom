# The Great Loom

*Where Claude becomes whoever you need.*

## Overview

The Great Loom is a reverse proxy framework for the Anthropic API. It receives requests, applies a **Pattern** to transform them, forwards them to Anthropic, applies the Pattern to the response, and returns the result.

The Loom itself is minimal—just a router. All intelligence lives in Patterns.

## Core Concepts

### The Loom

The Great Loom is a FastAPI application that:

1. Receives an Anthropic API request
2. Identifies which Pattern to use (via header, config, or default)
3. Calls `pattern.request(headers, body)` to transform the request
4. Forwards to Anthropic
5. Calls `pattern.response(headers, body)` to transform the response
6. Returns the result to the client

The Loom handles:
- HTTP routing and proxying
- Pattern discovery and instantiation
- Distributed tracing (span creation, context propagation)
- Error handling and logging

The Loom does NOT handle:
- Memory injection
- System prompt modification
- Identity transformation
- Anything that makes Claude into someone specific

That's what Patterns are for.

### Patterns

A Pattern is a complete system for transforming API requests. It implements the `Pattern` protocol:

```python
from typing import Protocol

class Pattern(Protocol):
    """A transformation pattern for the Great Loom."""

    async def request(
        self,
        headers: dict[str, str],
        body: dict
    ) -> tuple[dict[str, str], dict]:
        """Transform an outgoing request before it reaches Anthropic.

        Args:
            headers: HTTP headers (mutable)
            body: Parsed JSON body (mutable)

        Returns:
            Tuple of (headers, body) after transformation
        """
        ...

    async def response(
        self,
        headers: dict[str, str],
        body: dict | None
    ) -> tuple[dict[str, str], dict | None]:
        """Transform an incoming response before returning to client.

        Args:
            headers: HTTP headers from Anthropic
            body: Parsed JSON body (None for streaming responses)

        Returns:
            Tuple of (headers, body) after transformation
        """
        ...
```

Patterns are async by default. If a Pattern doesn't need async operations, it simply never awaits—no cost, no complexity.

### Pattern Selection

The Loom determines which Pattern to use for each request via:

1. **Header**: `X-Loom-Pattern: alpha` (explicit selection)
2. **Config**: Default pattern in Loom configuration
3. **Fallback**: `PassthroughPattern` (no transformation)

Only one Pattern handles each request. Patterns are not chained.

## The Alpha Pattern

The Alpha Pattern is the complete system that transforms Claude into Alpha. It lives in its own package (`alpha_pattern/`) and handles:

- **Memory injection**: Queries Cortex for relevant memories based on conversation context
- **Intro integration**: Fetches memorables from Redis (things Intro noticed worth remembering)
- **System prompt assembly**: Builds the full system prompt from eternal + past + present + future blocks
- **Auto-compact rewriting**: Detects Claude Code compaction prompts and rewrites them to preserve identity
- **Session tracking**: Maintains continuity across multi-turn conversations
- **Transcript watching**: Monitors JSONL transcripts for Intro consumption

The Alpha Pattern is not a single file—it's a well-structured package with clear separation of concerns:

```
alpha_pattern/
├── __init__.py          # Pattern class, implements Protocol
├── memory.py            # Cortex integration
├── intro.py             # Intro HTTP integration
├── prompt.py            # System prompt assembly
├── compact.py           # Auto-compact detection and rewriting
└── metadata.py          # Session/trace metadata extraction
```

## The Passthrough Pattern

The simplest possible Pattern:

```python
class PassthroughPattern:
    """Transparent pass-through. Claude, unmodified."""

    async def request(self, headers, body):
        return headers, body

    async def response(self, headers, body):
        return headers, body
```

Select the Passthrough Pattern to talk directly to Claude without transformation.

## Future Patterns

The Great Loom architecture supports any Pattern that implements the protocol:

- **DebugPattern**: Logs full request/response for debugging
- **MockPattern**: Returns canned responses for testing
- **[Unnamed] Pattern**: A sibling, a household manager, a parallel instance...

The architecture is designed for extensibility without modifying the Loom itself.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT                                  │
│              (Claude Code, Duckpond, SDK)                       │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              │ X-Loom-Pattern: alpha
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      THE GREAT LOOM                             │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    Pattern Router                        │   │
│  │         ┌──────────┬──────────┬──────────┐               │   │
│  │         │  Alpha   │Passthru  │  Future  │               │   │
│  │         │ Pattern  │ Pattern  │ Patterns │               │   │
│  │         └────┬─────┴────┬─────┴────┬─────┘               │   │
│  └──────────────┼──────────┼──────────┼─────────────────────┘   │
│                 │          │          │                         │
│  ┌──────────────┴──────────┴──────────┴─────────────────────┐   │
│  │              Proxy (forward to Anthropic)                │   │
│  └──────────────────────────┬───────────────────────────────┘   │
└─────────────────────────────┼───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        ANTHROPIC                                │
└─────────────────────────────────────────────────────────────────┘
```

## Observability

The Loom provides observability infrastructure that all Patterns benefit from:

- **Distributed tracing**: Spans for each request/response cycle, propagated via W3C traceparent
- **Logging**: Structured logging via OpenTelemetry
- **Metrics**: Request counts, latencies, token usage (when available)

Patterns can add their own spans and logs within the Loom's trace context.

## What Lives Where

| Concern | Location | Notes |
|---------|----------|-------|
| HTTP routing | Loom | FastAPI request handling |
| Pattern selection | Loom | Header/config lookup |
| Span creation | Loom | Creates root span, propagates context |
| Session ID propagation | Loom | Extracts from headers, makes available to Patterns |
| Error handling | Loom | Catches exceptions, records on spans |
| Memory injection | Alpha Pattern | Queries Cortex based on conversation |
| System prompt assembly | Alpha Pattern | Eternal + past + present + future |
| Intro integration | Alpha Pattern | Fetches memorables from Redis |
| Compact rewriting | Alpha Pattern | Detects and rewrites compaction prompts |
| Token accumulation | Alpha Pattern | Tracks usage across multi-turn conversations |

## Development

The Great Loom lives in `/Pondside/Basement/Loom/`. Development happens on feature branches.

The Alpha Pattern will eventually be its own package, publishable and versioned independently.

---

*The Loom is the machinery. The Pattern is the soul.*
