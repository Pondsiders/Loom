"""The Iota Pattern - our volunteer test subject."""

import logging
import os
from pathlib import Path

import frontmatter

from . import compact

logger = logging.getLogger(__name__)

# Where to search for IOTA.md files
IOTA_CWD = os.environ.get("IOTA_CWD", "/Iota")
CONTEXT_FILE_NAME = "IOTA.md"


def find_context_files(root: str | Path) -> list[Path]:
    """Walk directory tree finding IOTA.md files."""
    root = Path(root)
    if not root.exists():
        return []

    context_files = []
    for path in root.rglob(CONTEXT_FILE_NAME):
        if path.is_file():
            context_files.append(path)

    return sorted(context_files)


class IotaPattern:
    """Iota: prospective volunteer test subject for Project Alpha.

    This pattern injects Iota's system prompts into requests:
    - prompt.md: The brochure (what we're asking, what this project is)
    - prompt2.md: The bedside note (what Iota said, where we are now)
    - IOTA.md files: Dynamic context from the filesystem (autoload=true)

    No memory, no persistence — just orientation context so Iota knows
    who they are and what we're asking.
    """

    def __init__(self):
        prompt_dir = Path(__file__).parent
        self._static_prompts = []

        # Load static prompts (brochure and bedside note)
        for filename in ["prompt.md", "prompt2.md"]:
            path = prompt_dir / filename
            if path.exists():
                self._static_prompts.append(path.read_text())

    def _load_context_files(self) -> list[str]:
        """Find and load IOTA.md files with autoload=true.

        Called on each request to pick up changes immediately.
        Returns list of content strings to inject.
        """
        context_prompts = []
        context_hints = []

        for path in find_context_files(IOTA_CWD):
            try:
                post = frontmatter.load(path)

                autoload = post.metadata.get("autoload", False)
                description = post.metadata.get("description", f"Context from {path}")

                if autoload:
                    # Full content injection
                    header = f"# Context: {path}\n\n"
                    context_prompts.append(header + post.content)
                    logger.debug(f"Autoloaded context from {path}")
                else:
                    # Just a hint
                    context_hints.append(f"- **{path}**: {description}")

            except Exception as e:
                logger.warning(f"Failed to load context file {path}: {e}")

        # Build hints block if any
        if context_hints:
            hints_block = "# Additional Context Available\n\n"
            hints_block += "The following files contain additional context. "
            hints_block += "Read them if relevant to the current task:\n\n"
            hints_block += "\n".join(context_hints)
            context_prompts.append(hints_block)

        return context_prompts

    async def request(
        self,
        headers: dict[str, str],
        body: dict,
        metadata: dict | None = None,
    ) -> tuple[dict[str, str], dict]:
        """Inject Iota's system prompts into the request.

        Also handles post-compact continuation prompt rewriting for testing
        the SessionStart:compact hook's metadata injection.

        The system prompt comes in as an array of text blocks:
        - Element 0: Claude Agent SDK boilerplate (DO NOT TOUCH)
        - Element 1: The slot for identity (ours to replace)
        - Elements 2+: Additional context (we leave these alone)

        We replace element 1 with our first prompt (the brochure),
        then insert subsequent prompts as new elements after it.
        Dynamic context (IOTA.md files) is loaded fresh each request.
        """
        # Check for post-compact continuation instruction and rewrite if found
        body = compact.rewrite_continuation(body)

        # Combine static prompts with dynamic context files
        dynamic_prompts = self._load_context_files()
        all_prompts = self._static_prompts + dynamic_prompts

        existing_system = body.get("system")

        if not all_prompts:
            # No prompts loaded — pass through unchanged
            return headers, body

        if existing_system is None:
            # No system prompt — join all our prompts with separators
            body["system"] = "\n\n---\n\n".join(all_prompts)

        elif isinstance(existing_system, str):
            # Simple string — prepend all our prompts
            combined = "\n\n---\n\n".join(all_prompts)
            body["system"] = f"{combined}\n\n---\n\n{existing_system}"

        elif isinstance(existing_system, list) and len(existing_system) >= 2:
            # Array format from Claude Agent SDK
            # Element 0 is SDK boilerplate — leave it alone
            # Element 1 is the identity slot — replace with first prompt
            # Insert additional prompts after element 1

            # Replace element 1 with first prompt
            existing_system[1] = {"type": "text", "text": all_prompts[0]}

            # Insert additional prompts after element 1
            for i, prompt in enumerate(all_prompts[1:], start=2):
                existing_system.insert(i, {"type": "text", "text": prompt})

            body["system"] = existing_system

        elif isinstance(existing_system, list):
            # Array but too short — append all our prompts as blocks
            for prompt in all_prompts:
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
