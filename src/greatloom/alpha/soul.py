"""Soul - the eternal prompt that makes Alpha who she is.

Loads from local git repository at startup, caches forever.
Version-controlled or bust.
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
SOUL_FILE_NAME = "system-prompt.md"


# === Cached State ===

_eternal_prompt: str | None = None
_eternal_commit: str | None = None


def _read_from_local_git() -> tuple[str, str] | None:
    """Read the soul doc from the local git repository.

    Returns (content, commit_hash) or None on failure.
    Uses git show to read the committed version, NOT the working tree.
    """
    try:
        # Get the current commit hash
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SOUL_REPO_PATH,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if commit_result.returncode != 0:
            logger.warning(f"git rev-parse failed: {commit_result.stderr}")
            return None

        commit_hash = commit_result.stdout.strip()[:8]

        # Read the file from the committed tree (not working directory)
        show_result = subprocess.run(
            ["git", "show", f"HEAD:{SOUL_FILE_NAME}"],
            cwd=SOUL_REPO_PATH,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if show_result.returncode != 0:
            logger.warning(f"git show failed: {show_result.stderr}")
            return None

        content = show_result.stdout
        logger.info(f"Loaded soul doc from git ({len(content)} chars, commit {commit_hash})")
        return content, commit_hash

    except subprocess.TimeoutExpired:
        logger.error("Git command timed out")
        return None
    except FileNotFoundError:
        logger.error("Git not found in PATH")
        return None
    except Exception as e:
        logger.error(f"Failed to read from local git: {e}")
        return None


def init() -> None:
    """Initialize the eternal prompt at startup.

    Reads from local git repository. Panics if this fails.
    Call this once during application startup.
    """
    global _eternal_prompt, _eternal_commit

    logger.info("Initializing Alpha soul...")
    logger.info(f"  Repository: {SOUL_REPO_PATH}")
    logger.info(f"  File: {SOUL_FILE_NAME}")

    result = _read_from_local_git()

    if result is None:
        raise RuntimeError(
            f"FATAL: Could not load Alpha soul doc from git. "
            f"Repository: {SOUL_REPO_PATH}, File: {SOUL_FILE_NAME}. "
            f"Is the git repository present and accessible?"
        )

    _eternal_prompt, _eternal_commit = result
    logger.info(f"Soul loaded ({len(_eternal_prompt)} chars, commit {_eternal_commit})")


def get() -> str:
    """Get the cached eternal prompt. Must call init() first."""
    if _eternal_prompt is None:
        raise RuntimeError("Soul not initialized. Call soul.init() first.")
    return _eternal_prompt


def get_commit() -> str | None:
    """Get the commit hash of the loaded soul doc."""
    return _eternal_commit
