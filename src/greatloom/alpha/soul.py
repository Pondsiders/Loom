"""Soul - the eternal prompts that make Alpha who she is.

Loads from local git repository at startup, caches forever.
Version-controlled or bust.

Supports two documents:
- system-prompt.md (the soul doc)
- compact-prompt.md (how to survive compactions)

Both loaded from the same repo, same ref (unless overridden).
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# === Configuration ===

SOUL_REPO_PATH = Path(os.environ.get(
    "ALPHA_SOUL_REPO",
    "/Pondside/Alpha-Home/self/system-prompt"
))

# Git refs for each document
# "@latest" means HEAD, otherwise use the specified tag/commit
SOUL_REF = os.environ.get("ALPHA_SOUL_REF", "@latest")
COMPACT_REF = os.environ.get("ALPHA_COMPACT_REF", "@latest")

# File names within the repo
SOUL_FILE = "system-prompt.md"
COMPACT_FILE = "compact-prompt.md"


# === Cached State ===

_soul_prompt: str | None = None
_soul_commit: str | None = None
_compact_prompt: str | None = None
_compact_commit: str | None = None


def _resolve_ref(ref: str) -> str:
    """Convert our ref format to git ref.

    "@latest" -> "HEAD"
    "v26.109" -> "v26.109"
    """
    if ref == "@latest":
        return "HEAD"
    return ref


def _read_from_git(filename: str, ref: str) -> tuple[str, str] | None:
    """Read a file from the git repository at a specific ref.

    Args:
        filename: Name of the file in the repo
        ref: Git ref to read from ("HEAD", tag name, commit hash)

    Returns:
        (content, resolved_commit_hash) or None on failure.
    """
    git_ref = _resolve_ref(ref)

    try:
        # Resolve the ref to a commit hash
        commit_result = subprocess.run(
            ["git", "rev-parse", git_ref],
            cwd=SOUL_REPO_PATH,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if commit_result.returncode != 0:
            logger.warning(f"git rev-parse {git_ref} failed: {commit_result.stderr}")
            return None

        commit_hash = commit_result.stdout.strip()[:8]

        # Read the file from that commit
        show_result = subprocess.run(
            ["git", "show", f"{git_ref}:{filename}"],
            cwd=SOUL_REPO_PATH,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if show_result.returncode != 0:
            logger.warning(f"git show {git_ref}:{filename} failed: {show_result.stderr}")
            return None

        content = show_result.stdout
        logger.info(f"Loaded {filename} from git (ref={ref}, commit={commit_hash}, {len(content)} chars)")
        return content, commit_hash

    except subprocess.TimeoutExpired:
        logger.error("Git command timed out")
        return None
    except FileNotFoundError:
        logger.error("Git not found in PATH")
        return None
    except Exception as e:
        logger.error(f"Failed to read {filename} from git: {e}")
        return None


def init() -> None:
    """Initialize the eternal prompts at startup.

    Reads both soul doc and compact prompt from git.
    Panics if soul doc fails (it's required).
    Warns if compact prompt fails (graceful degradation).

    Call this once during application startup.
    """
    global _soul_prompt, _soul_commit, _compact_prompt, _compact_commit

    logger.info("Initializing Alpha soul...")
    logger.info(f"  Repository: {SOUL_REPO_PATH}")
    logger.info(f"  Soul ref: {SOUL_REF}")
    logger.info(f"  Compact ref: {COMPACT_REF}")

    # Load soul doc (required)
    soul_result = _read_from_git(SOUL_FILE, SOUL_REF)
    if soul_result is None:
        raise RuntimeError(
            f"FATAL: Could not load Alpha soul doc from git. "
            f"Repository: {SOUL_REPO_PATH}, File: {SOUL_FILE}, Ref: {SOUL_REF}. "
            f"Is the git repository present and accessible?"
        )
    _soul_prompt, _soul_commit = soul_result

    # Load compact prompt (optional, graceful degradation)
    compact_result = _read_from_git(COMPACT_FILE, COMPACT_REF)
    if compact_result is None:
        logger.warning(
            f"Could not load compact prompt from git. "
            f"Compact handling will use fallback. "
            f"File: {COMPACT_FILE}, Ref: {COMPACT_REF}"
        )
        _compact_prompt = None
        _compact_commit = None
    else:
        _compact_prompt, _compact_commit = compact_result

    logger.info(f"Soul initialized: soul={_soul_commit}, compact={_compact_commit or 'fallback'}")


def get_soul() -> str:
    """Get the cached soul doc. Must call init() first."""
    if _soul_prompt is None:
        raise RuntimeError("Soul not initialized. Call soul.init() first.")
    return _soul_prompt


def get_compact() -> str | None:
    """Get the cached compact prompt, or None if not loaded."""
    return _compact_prompt


def get_soul_commit() -> str | None:
    """Get the commit hash of the loaded soul doc."""
    return _soul_commit


def get_compact_commit() -> str | None:
    """Get the commit hash of the loaded compact prompt."""
    return _compact_commit


# Backwards compatibility
def get() -> str:
    """Alias for get_soul(). Deprecated, use get_soul() instead."""
    return get_soul()
