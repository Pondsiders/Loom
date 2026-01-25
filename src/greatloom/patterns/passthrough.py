"""The Passthrough Pattern - transparent pass-through, no transformation."""


class PassthroughPattern:
    """Transparent pass-through. Claude, unmodified.

    The simplest possible pattern. Receives a request, returns it unchanged.
    Receives a response, returns it unchanged. No memory injection, no
    system prompt modification, no identity transformation.

    Use this pattern to talk directly to Claude without the Loom
    doing anything at all.
    """

    async def request(
        self,
        headers: dict[str, str],
        body: dict,
        metadata: dict | None = None,
    ) -> tuple[dict[str, str], dict]:
        """Pass through unchanged."""
        return headers, body

    async def response(
        self,
        headers: dict[str, str],
        body: dict | None,
    ) -> tuple[dict[str, str], dict | None]:
        """Pass through unchanged."""
        return headers, body
