"""Centralized version for SelenaCore.

Format: MAJOR.MINOR.PATCH-LABEL+COMMIT
- MAJOR.MINOR — manual, bumped per release cycle
- PATCH       — auto, from `git rev-list --count HEAD` (total commit count)
- LABEL       — "beta" | "rc" | "" (release)
- COMMIT      — short git SHA (7 chars), appended as build metadata

Example: 0.3.142-beta+0644435

Resolution order:
1. Try `git` commands (works on host or if git is installed in container)
2. Fall back to /opt/selena-core/.version file (written at deploy time)
3. Return MAJOR.MINOR.0-LABEL as last resort
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

MAJOR = 0
MINOR = 3
LABEL = "beta"

_VERSION_FILE = Path("/opt/selena-core/.version")


def _git(args: list[str]) -> str:
    """Run a git command and return stripped stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=5,
            cwd="/opt/selena-core",
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _get_patch() -> int:
    """Return total commit count as patch number."""
    count = _git(["rev-list", "--count", "HEAD"])
    try:
        return int(count)
    except (ValueError, TypeError):
        return 0


def _get_commit_hash() -> str:
    """Return short (7-char) commit SHA."""
    return _git(["rev-parse", "--short", "HEAD"])


def _read_version_file() -> str:
    """Read pre-computed version from .version file (written at deploy time)."""
    try:
        return _VERSION_FILE.read_text().strip()
    except Exception:
        return ""


def get_version() -> str:
    """Return full version string, e.g. '0.3.142-beta+0644435'."""
    # Try git first
    patch = _get_patch()
    commit = _get_commit_hash()

    if patch or commit:
        base = f"{MAJOR}.{MINOR}.{patch}"
        if LABEL:
            base += f"-{LABEL}"
        if commit:
            base += f"+{commit}"
        return base

    # Fall back to .version file (written by deploy script)
    from_file = _read_version_file()
    if from_file:
        return from_file

    # Last resort
    base = f"{MAJOR}.{MINOR}.0"
    if LABEL:
        base += f"-{LABEL}"
    return base


# Cached at import time
VERSION: str = get_version()
VERSION: str = get_version()
