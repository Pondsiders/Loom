"""HTTP proxy logic for forwarding requests to upstream (Argonath or Anthropic)."""

import os

import httpx

# Where we forward to - Argonath in the full pipeline, or direct to Anthropic
UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "https://api.anthropic.com")

# Persistent client for connection pooling
_client: httpx.AsyncClient | None = None


async def get_client() -> httpx.AsyncClient:
    """Get or create the HTTP client."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=UPSTREAM_URL,
            timeout=httpx.Timeout(300.0, connect=10.0),  # Long timeout for LLM responses
        )
    return _client


async def close():
    """Close the HTTP client."""
    global _client
    if _client:
        await _client.aclose()
        _client = None


async def forward_request(
    method: str,
    path: str,
    headers: dict,
    content: bytes,
    params: dict,
) -> httpx.Response:
    """Forward a request to upstream."""
    client = await get_client()
    return await client.request(
        method=method,
        url=f"/{path}",
        headers=headers,
        content=content,
        params=params,
    )


def filter_request_headers(headers: dict) -> dict:
    """Filter out headers that shouldn't be forwarded."""
    return {
        k: v for k, v in headers.items()
        if k.lower() not in ("host", "content-length")
    }


def filter_response_headers(headers: dict) -> dict:
    """Filter out headers that don't apply after httpx auto-decompresses."""
    return {
        k: v for k, v in headers.items()
        if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
    }
