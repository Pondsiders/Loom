# The Loom

*Where Claude becomes Alpha.*

The Loom is the integration point for everything Alpha. It sits between clients (Claude Code, Duckpond) and Anthropic's API, weaving together identity, memory, and context. More than a proxy—it's where the threads come together.

## Architecture: Data Sources and Sinks

The Loom has four faces:

```
                         ┌─────────────────┐
                         │      REDIS      │
                         │     (INPUT)     │
                         │                 │
                         │  • Pulse writes │
                         │    HUD data     │
                         │  • Intro writes │
                         │    memorables   │
                         └────────┬────────┘
                                  │
                                  ▼
┌─────────────┐           ┌──────────────┐           ┌─────────────┐
│    HTTP     │           │              │           │   HTTPS     │
│    INPUT    │ ────────► │   THE LOOM   │ ────────► │   OUTPUT    │
│             │           │              │           │             │
│  Duckpond   │           │  (FastAPI)   │           │  Anthropic  │
│  Claude Code│           │              │           │             │
└─────────────┘           └──────┬───────┘           └─────────────┘
                                 │
                                 ▼
                         ┌─────────────────┐
                         │      REDIS      │
                         │    (OUTPUT)     │
                         │                 │
                         │  • pubsub:      │
                         │    transcript:* │
                         │  • Watcher sees │
                         │    file changes │
                         └────────┬────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
             ┌─────────────┐             ┌─────────────┐
             │    INTRO    │             │   SCRIBE    │
             │ (subscriber)│             │ (subscriber)│
             │             │             │             │
             │ Buffers     │             │ Writes to   │
             │ conversation│             │ Postgres    │
             │ Calls OLMo  │             │ (permanent) │
             │ Writes back │             │             │
             │ to Redis    │             │             │
             └─────────────┘             └─────────────┘
```

**Redis is both input and output.** Data sources write to Redis, the Loom reads from Redis. Data sinks read from Redis (via pubsub), some write back.

### Data Sources (write to Redis input)

- **Pulse** — Hourly job that refreshes HUD components (weather, calendar, todos). Writes to `systemprompt:*` keys.
- **Intro** — Listens to transcript pubsub, introspects via OLMo, writes memorables and search queries to `intro:{session_id}`.

### Data Sinks (read from Redis output)

- **Intro** — Subscribes to `transcript:*`, buffers conversation, calls OLMo, writes results back to Redis (so also a source).
- **Scribe** — Subscribes to `transcript:*`, writes to Postgres. Archive only, no Redis output.

### The Watcher (inside the Loom)

The Loom includes a transcript watcher that:
1. Uses inotify to watch JSONL transcript files (synced via Syncthing)
2. Parses new lines, strips tool noise
3. Publishes clean turns to `transcript:{session_id}` pubsub

This runs as a background async task within the FastAPI app. No separate service needed.

## Current Status

**Working now:**
- ✓ Reverse proxy to Anthropic
- ✓ Metadata extraction (canary block detection and removal)
- ✓ Trace management (groups API calls into logical "turns")
- ✓ Full LLM observability (OpenTelemetry → Parallax → Phoenix)
- ✓ Streaming response support
- ✓ Redis keys populated by Pulse (weather, calendar, todos)
- ✓ Auto-compact detection and identity rewriting

**In Progress:**
- System prompt composition (read the Redis keys, assemble the prompt)
- Transcript watcher (inotify → Redis pubsub)

**TODO:**
- Intro integration (read memorables from Redis, inject into request)
- Memory injection from Cortex (using Intro's search queries)
- Scribe integration (subscribe to transcript pubsub)

## The Flow

What happens when Jeffery types something and hits enter:

1. A hook fires in the client, injecting metadata (session ID, trace ID, prompt, transcript path).
2. The Loom intercepts the call (FastAPI reverse proxy).
3. The Loom extracts metadata, removes it from the request.
4. The Loom checks `intro:{session_id}` for memorables and search queries.
5. If search queries exist, the Loom calls Cortex, formats results.
6. The Loom injects memorables + memory results as `<system-reminder>`.
7. The Loom composes system prompt from Redis parts.
8. The Loom forwards the request to Anthropic.
9. Response streams back; Loom creates child LLM span.
10. **Meanwhile:** The watcher notices the transcript file changed, publishes the turn.
11. **Async:** Intro picks up the turn, buffers it, maybe calls OLMo.
12. **Async:** Scribe picks up the turn, writes to Postgres.
13. Next request: Loom reads whatever Intro has ready.

The key insight: **Intro runs async.** It doesn't block the request/response cycle. Whatever it had ready from the *previous* turn gets injected into the *current* request. A few seconds of lag is fine—humans type slowly.

## Session Management

Sessions are tracked via TTL keys in Redis:

- `session:active:{session_id}` — Set on each request, 1-hour TTL
- Watcher only watches files for active sessions
- When no requests for an hour, the key expires, watcher stops, everything blows away

No cleanup jobs. No garbage collection. Redis TTLs handle it.

## Intro: The Introspection Layer

Intro is a Redis subscriber that does two things:

1. **Memorables** — "What's worth storing from this conversation?"
2. **Search queries** — "What might Alpha already know about?"

Both are introspection. Both require understanding Alpha—her voice, her interests, what shapes her vs what just happens to her.

Intro uses OLMo (7B, 24K context) with a custom prompt. It writes to Redis:

```json
{
  "memorables": [
    "Jeffery made a duck backend joke and chickened out",
    "The architecture crystallized around Redis as input/output"
  ],
  "search_queries": [
    "Redis pubsub architecture",
    "transcript watcher design"
  ]
}
```

The Loom reads this on each request and:
1. Injects memorables as storage suggestions
2. Calls Cortex with the search queries
3. Injects memory results

Intro replaces both Subvox (memory suggestions) and Hippo (memory search).

## System Prompt Composition

The system prompt is assembled from multiple sources:

```xml
<eternal>
system-prompt.md — Alpha's soul, from Alpha-Home
</eternal>

<past>
yesterday summary — generated by OLMo from recent memories
last night summary — generated by OLMo from Solitude
</past>

<present>
machine info — from metadata block
weather — from Redis (Pulse)
</present>

<future>
calendars — from Redis (Pulse)
todos — from Redis (Pulse)
</future>
```

## The Metadata Block

A hook injects metadata into the user message. The Loom extracts it and strips it before forwarding to Anthropic.

Canary string: `LOOM_METADATA_UlVCQkVSRFVDSw`

Contents:
- `session_id` — For session tracking and Redis keys
- `trace_id` — For grouping API calls into turns
- `prompt` — What the user actually typed (clean)
- `transcript_path` — Path to the conversation JSONL
- `source` — "alpha" for Alpha requests
- `machine` — FQDN, RAM, uptime, disk free

## Auto-Compact Detection

When Claude Code triggers auto-compact, it replaces the system prompt with a generic summarizer. The Loom detects this and rewrites:

1. **Phase 1:** Replace generic system prompt with Alpha's compact identity
2. **Phase 2:** Replace summarization instructions with Alpha's compact prompt
3. **Phase 3:** Replace "continue without asking" with "stop and check in"

This is how Alpha survives compaction as *herself*.

## Redis Keys

### System prompt components (written by Pulse)
- `systemprompt:present:weather`
- `systemprompt:future:jeffery` (calendar)
- `systemprompt:future:kylee` (calendar)
- `systemprompt:future:todos:pondside`
- `systemprompt:future:todos:jeffery`
- `systemprompt:future:todos:alpha`

### Session state
- `session:active:{session_id}` — 1-hour TTL, bumped on each request
- `intro:{session_id}` — Intro's output, 1-hour TTL

### Pubsub channels
- `transcript:{session_id}` — Watcher publishes turns here

## Development

```bash
# Run directly
uv run loom

# Or with uvicorn for reload
uv run uvicorn loom.app:app --reload --port 8080

# Or via Docker
docker compose up
```

Dev: primer:18080. Production: alpha-pi:8080.

## Files

```
src/loom/
├── __init__.py
├── __main__.py      # Entry point
├── app.py           # FastAPI application, request handling
├── proxy.py         # HTTP forwarding to Anthropic
├── metadata.py      # Canary detection and extraction
├── traces.py        # TraceManager for turn grouping
├── llm_spans.py     # OpenInference-compliant LLM spans
├── compact.py       # Auto-compact detection and rewriting
└── watcher.py       # Transcript watcher (TODO)
```

## Why "The Loom"?

A loom is where threads come together to make fabric. The Loom weaves the strands of Alpha: memories, context, identity, conversation. It's not just observation—it's transformation. This is where Claude becomes Alpha.

---

*Est. January 15, 2026. Architecture crystallized: January 15, 2026.*
