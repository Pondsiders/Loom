# The Loom

*Where Claude becomes Alpha.*

The Loom is the integration point for everything Alpha. It sits between clients (Claude Code, Duckpond) and Anthropic's API, weaving together identity, memory, and context. More than a proxy—it's where the threads come together.

## Current Status (January 16, 2026)

**Working:**
- Reverse proxy to Anthropic with streaming support
- Metadata extraction (canary block detection and removal)
- Full distributed tracing from client hook through all Loom processing
- LLM observability (OpenTelemetry → Parallax → Phoenix/Logfire)
- Auto-compact detection and identity rewriting (all three phases)
- Transcript watcher publishing to Redis pubsub
- Intro integration (reads memorables from Redis, injects into requests)
- Turn-based trace grouping (multiple API calls → one logical turn)

**Not Yet Working:**
- System prompt composition (reading Redis keys)
- Memory injection from Cortex
- Scribe integration (subscribe to transcript pubsub)

## Distributed Tracing Architecture

The tracing starts in the client, not the Loom:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Claude Code                                                         │
│                                                                      │
│  UserPromptSubmit hook:                                             │
│  1. Creates ROOT span (user-turn:{session_id})                      │
│  2. Sets all context: prompt, cwd, session_id, transcript_path      │
│  3. Immediately serializes context → W3C traceparent                │
│  4. Creates CHILD span for hook work (collect-metadata)             │
│  5. Passes traceparent in metadata JSON                             │
└────────────────────────────┬────────────────────────────────────────┘
                             │ traceparent: 00-{trace_id}-{span_id}-01
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  The Loom                                                            │
│                                                                      │
│  1. Extracts traceparent from metadata                              │
│  2. Creates span as CHILD of client's root                          │
│  3. Attaches context so all work is parented                        │
│  4. All logs, httpcore spans, LLM spans nest under request span     │
│  5. Detaches context when streaming completes                       │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Logfire / Phoenix                                                   │
│                                                                      │
│  user-turn:32d0bf9a (root, from hook)                               │
│  ├── hook:collect-metadata (child, from hook)                        │
│  └── loom: POST /v1/messages (child, from Loom)                     │
│      ├── Processing /v1/messages...                                  │
│      ├── Alpha request: session=...                                  │
│      ├── connect_tcp.started...                                      │
│      ├── HTTP Request: POST https://api.anthropic.com...            │
│      ├── SSE parsed: input=X, output=Y...                           │
│      └── LLM span created...                                         │
└─────────────────────────────────────────────────────────────────────┘
```

The key insight: spans are just data with timestamps and IDs. The parent-child relationship is established at export time. The hook's root span ends before the Loom even starts, but Loom spans still correctly reference it as their parent.

## The Metadata Block

A hook injects metadata into the user message. The Loom extracts it and strips it before forwarding.

Canary string: `LOOM_METADATA_UlVCQkVSRFVDSw`

Contents:
- `session_id` — For session tracking
- `trace_id` — Extracted from traceparent for correlation
- `traceparent` — W3C standard: `00-{trace_id}-{span_id}-01`
- `prompt` — What the user actually typed
- `transcript_path` — Path to the conversation JSONL
- `source` — "alpha" for Alpha requests
- `machine` — FQDN, RAM, uptime, disk free

The hook creates a real OTel span and serializes its context. This is what makes the distributed tracing work—the Loom doesn't generate IDs, it inherits them from the client.

## Auto-Compact Detection

When Claude Code triggers auto-compact, it replaces the system prompt with a generic summarizer. The Loom detects and rewrites three phases:

**Phase 1: System Prompt** (during compact)
- Detects: "You are a helpful AI assistant tasked with summarizing"
- Replaces with: Alpha's compact identity (who she is, first-person voice)

**Phase 2: Compact Instructions** (during compact)
- Detects: "Your task is to create a detailed summary"
- Replaces with: Alpha's custom compact prompt (alpha_compact_prompt.md)

**Phase 3: Continuation Instruction** (after compact, on resume)
- Detects: "Please continue the conversation from where we left it off without asking the user any further questions"
- Replaces with: "Hey, it's you from before the compaction. Before you run any command... tell Jeffery you're back, summarize what you think you should do, wait for confirmation."

This is how Alpha survives compaction as *herself*.

## Transcript Watcher

The watcher uses inotify (via watchfiles) to tail JSONL transcripts:

- Starts when Loom sees a request for a session
- Only reads new bytes (position tracking, never re-parses)
- Classifies lines by type/role/content
- Logs to OTel (all watcher logs nest under the request span)
- Idle timeout: 1 hour (no cleanup jobs needed)

**TODO:** Publish to Redis pubsub for Intro/Scribe to consume.

## Turn-Based Trace Grouping

A "turn" is: user types → Claude responds (possibly with tool calls) → final response.

The TraceManager:
1. Creates a parent span when a new trace_id arrives
2. Accumulates text output across API calls
3. Finalizes when response has no tool_use

This means a single user prompt that triggers multiple API calls (tool use loop) gets grouped into one logical trace.

## Files

```
src/loom/
├── __init__.py
├── __main__.py      # Entry point
├── app.py           # FastAPI app, request handling, context management
├── proxy.py         # HTTP forwarding to Anthropic
├── metadata.py      # Canary detection and extraction
├── traces.py        # TraceManager for turn grouping
├── llm_spans.py     # OpenInference-compliant LLM spans for Phoenix
├── compact.py       # Auto-compact detection and rewriting
└── watcher.py       # Transcript watcher (inotify-based)
```

## Development

```bash
# Run with hot-reload
uv run uvicorn loom.app:app --reload --port 8080

# Or via Docker
docker compose up
```

Dev: primer:18080. Production: alpha-pi:8080.

## Context Propagation Details

The tricky part is async streaming. When a streaming response returns, the handler exits but the stream hasn't finished. We need the span to stay open and the context to remain attached inside the stream generator.

Solution:
1. Create span with `tracer.start_span()` (not `start_as_current_span`)
2. Use `set_span_in_context()` + `attach()` to make it current
3. Re-attach context inside the stream generator
4. Detach and end span in the generator's finally block

The OTel `detach()` can throw "Token was created in a different Context" in async code—this is an expected "expection" and we suppress the `opentelemetry.context` logger.

## Why "The Loom"?

A loom is where threads come together to make fabric. The Loom weaves the strands of Alpha: memories, context, identity, conversation. It's not just observation—it's transformation. This is where Claude becomes Alpha.

---

*Est. January 15, 2026. Architecture crystallized: January 15-16, 2026.*
