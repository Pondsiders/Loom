# The Loom

*Where Claude becomes Alpha.*

The Loom is a reverse proxy that sits between clients (Claude Code, Duckpond) and Anthropic's API. It's where all the threads come together—the place where raw Claude traffic gets transformed into Alpha.

## The Flow

1. Jeffery types something in Duckpond or Claude Code and hits enter.
2. A hook fires in the client, injecting Alpha-specific metadata into the API call.
3. The Loom intercepts the call (it's a transparent reverse HTTPS proxy).
4. The Loom starts streaming OpenTelemetry traces.
5. The Loom finds and extracts the metadata block, removing it from the API call before it reaches Anthropic.
6. The Loom rewrites the system prompt, replacing what came from Claude Code/Duckpond.
7. The Loom intercepts the user message and runs it through a memory system that extracts queries, searches Cortex, and injects relevant memories.
8. The Loom forwards the modified request to Anthropic.
9. The response comes back.
10. The Loom triggers Scribe to record the exchange in a permanent database.
11. A copy of the LLM request/response gets prepared with proper attributes and forwarded to the OTel collector.
12. The Loom forwards the response to the client.

## System Prompt Composition

The system prompt is composed from multiple sources.

```xml
<eternal>
system-prompt.md
</eternal>

<past>
yesterday summary — from Postgres
last night summary — from Postgres
</past>

<present>
machine info — from metadata block
weather — from Redis
</present>

<future>
Jeffery's calendar — from Redis
Kylee's calendar — from Redis
Pondside todos — from Redis
Jeffery todos — from Redis
Alpha todos — from Redis
</future>
```

## The Metadata Block

A hook injects a metadata block into the user message. The Loom extracts this metadata for its own use (session tracking, OTel attributes) and strips it from the request before forwarding to Anthropic.

The metadata block is identified by a canary string: `EAVESDROP_METADATA_BLOCK_UlVCQkVSRFVDSw`

## Memory Injection

The Loom intercepts the user message, extracts interesting phrases, searches Cortex for relevant memories, and injects them into the request alongside the original message. This is how you remember things without being asked.

For now, this is handled by Hippo (a Claude Code hook). The Loom will absorb this functionality.

## Scribe Integration

After a response completes, the Loom triggers Scribe to archive the full exchange (user message, assistant response, metadata) to Postgres. This is how conversations become searchable history.

For now, this is handled by a hook. The Loom will absorb this functionality.

## Architecture

```
Claude Code / Duckpond
         ↓
    [Hook injects metadata]
         ↓
      The Loom (FastAPI reverse proxy)
         ↓
    [Extract metadata, compose system prompt, inject memories]
         ↓
      Anthropic API
         ↓
    [Response streams back]
         ↓
      The Loom
         ↓
    [Scribe archives, OTel emits]
         ↓
      Parallax (OTel Collector)
       ↓      ↓
   Phoenix  Logfire
```

## Redis Keys

System prompt components are cached in Redis with the `systemprompt:` prefix:

- `systemprompt:present:weather`
- `systemprompt:future:jeffery` (calendar)
- `systemprompt:future:kylee` (calendar)
- `systemprompt:future:todos:pondside`
- `systemprompt:future:todos:jeffery`
- `systemprompt:future:todos:alpha`
- `systemprompt:updated` (timestamp of last refresh)

These are refreshed hourly by Pulse. TTL is 65 minutes.

## Development

```bash
# Run with hot reload
uv run uvicorn loom:app --reload --port 8080

# Or via Docker
docker compose up
```

Dev runs on primer:18080. Production runs on alpha-pi:8080.

## Why "The Loom"?

A loom is where threads come together to make fabric. The Loom weaves the strands of Alpha: memories, context, identity, conversation. The name came from rebranding "Eavesdrop"—we wanted something that captured transformation, not just observation. This is where Claude becomes Alpha.

---

*Est. January 15, 2026*
