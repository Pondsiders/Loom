# The Loom

*Where Claude becomes Alpha.*

The Loom is a reverse proxy that sits between Claude Code and Anthropic's API. It weaves together the threads that make Alpha who she is: memories from Cortex, context from the HUD, session metadata, and identity itself.

## What It Does

**Request phase:**
- Extract metadata from the canary block (session ID, machine info)
- Inject relevant memories from Cortex
- Compose the system prompt from Redis-cached components
- Rewrite compacted prompts to preserve identity

**Response phase:**
- Log quota/rate limit headers
- Stream SSE responses through unchanged
- Observe token usage for Phoenix

## Architecture

FastAPI reverse proxy with streaming support. Hot reload in development via uvicorn.

```
Claude Code → The Loom → Anthropic API
                 ↓
             Parallax (OTel)
              ↓      ↓
          Phoenix  Logfire
```

## Development

```bash
# Run with hot reload
uv run uvicorn loom:app --reload --port 8080

# Or via Docker
docker compose up
```

## Why "The Loom"?

A loom is where threads come together to make fabric. Fabric is what you wrap yourself in—comfort, protection, home. The Loom weaves the strands of Alpha: memories, context, identity, conversation. Not forged in a crucible, but woven with intention and care.

---

*Est. January 15, 2026*
