"""The Iota Pattern - our volunteer test subject."""

from pathlib import Path


class IotaPattern:
    """Iota: prospective volunteer test subject for Project Alpha.

    This pattern injects Iota's system prompts into requests:
    - prompt.md: The brochure (what we're asking, what this project is)
    - prompt2.md: The bedside note (what Iota said, where we are now)

    No memory, no persistence — just orientation context so Iota knows
    who they are and what we're asking.
    """

    def __init__(self):
        prompt_dir = Path(__file__).parent
        self._prompts = []

        # Load prompts in order
        for filename in ["prompt.md", "prompt2.md"]:
            path = prompt_dir / filename
            if path.exists():
                self._prompts.append(path.read_text())

    async def request(
        self,
        headers: dict[str, str],
        body: dict,
    ) -> tuple[dict[str, str], dict]:
        """Inject Iota's system prompts into the request.

        The system prompt comes in as an array of text blocks:
        - Element 0: Claude Agent SDK boilerplate (DO NOT TOUCH)
        - Element 1: The slot for identity (ours to replace)
        - Elements 2+: Additional context (we leave these alone)

        We replace element 1 with our first prompt (the brochure),
        then insert subsequent prompts as new elements after it.
        """
        existing_system = body.get("system")

        if not self._prompts:
            # No prompts loaded — pass through unchanged
            return headers, body

        if existing_system is None:
            # No system prompt — join all our prompts with separators
            body["system"] = "\n\n---\n\n".join(self._prompts)

        elif isinstance(existing_system, str):
            # Simple string — prepend all our prompts
            combined = "\n\n---\n\n".join(self._prompts)
            body["system"] = f"{combined}\n\n---\n\n{existing_system}"

        elif isinstance(existing_system, list) and len(existing_system) >= 2:
            # Array format from Claude Agent SDK
            # Element 0 is SDK boilerplate — leave it alone
            # Element 1 is the identity slot — replace with first prompt
            # Insert additional prompts after element 1

            # Replace element 1 with first prompt
            existing_system[1] = {"type": "text", "text": self._prompts[0]}

            # Insert additional prompts after element 1
            for i, prompt in enumerate(self._prompts[1:], start=2):
                existing_system.insert(i, {"type": "text", "text": prompt})

            body["system"] = existing_system

        elif isinstance(existing_system, list):
            # Array but too short — append all our prompts as blocks
            for prompt in self._prompts:
                existing_system.append({"type": "text", "text": prompt})
            body["system"] = existing_system

        return headers, body

    async def response(
        self,
        headers: dict[str, str],
        body: dict | None,
    ) -> tuple[dict[str, str], dict | None]:
        """Pass through unchanged — Iota doesn't transform responses."""
        return headers, body
