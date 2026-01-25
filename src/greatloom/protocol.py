"""The Pattern protocol - the contract all patterns must fulfill."""

from typing import Protocol


class Pattern(Protocol):
    """A transformation pattern for the Great Loom.

    Patterns are the soul of the Great Loom. They transform requests
    before they reach Anthropic and responses before they return to
    the client. The Loom itself is just machinery - Patterns are
    where identity lives.
    """

    async def request(
        self,
        headers: dict[str, str],
        body: dict,
        metadata: dict | None = None,
    ) -> tuple[dict[str, str], dict]:
        """Transform an outgoing request before it reaches Anthropic.

        Args:
            headers: HTTP headers (mutable)
            body: Parsed JSON body (mutable)
            metadata: Extracted metadata from the request (memories, etc.)

        Returns:
            Tuple of (headers, body) after transformation
        """
        ...

    async def response(
        self,
        headers: dict[str, str],
        body: dict | None,
    ) -> tuple[dict[str, str], dict | None]:
        """Transform an incoming response before returning to client.

        For streaming responses, body will be None (we can't transform
        the stream, only observe it). For non-streaming responses,
        body will be the parsed JSON.

        Args:
            headers: HTTP headers from Anthropic
            body: Parsed JSON body (None for streaming responses)

        Returns:
            Tuple of (headers, body) after transformation
        """
        ...
