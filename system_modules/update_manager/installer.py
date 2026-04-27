"""Pre-flight + dispatch helpers for update_manager.

Lives in the smarthome-core process under a ReadOnlyPaths sandbox; therefore
this module performs only **read-only** validation and **dispatches** the
heavy lifting to ``scripts/apply-update.sh`` running in its own systemd
transient unit. See :mod:`system_modules.update_manager.updater` for the
high-level flow.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


APPLY_UPDATE_SCRIPT = "/opt/selena-core/scripts/apply-update.sh"
INSTALL_LOCK_PATH = Path("/var/lib/selena/update/.install_lock")
UPDATE_FLAG_PATH = Path("/secure/.update_in_progress")
DEFAULT_LOG_PATH = Path("/var/log/selena/update.log")


def sanitize_unit_tag(tag: str) -> str:
    """Sanitize a tag for use as a systemd unit name suffix.

    Semver build metadata uses '+' (e.g. ``0.4.142-rc+0644435``) which is not
    valid in unit names. Map any character outside ``[a-zA-Z0-9-]`` to ``_``.
    """
    return re.sub(r"[^a-zA-Z0-9-]", "_", tag)


def _parse_python_version(spec: str) -> tuple[int, int]:
    """Parse "3.11" or "3.11.4" → (3, 11). Defaults to (3, 0) on parse error."""
    parts = spec.strip().split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return (3, 0)


def _dir_size_bytes(path: Path) -> int:
    """Best-effort recursive size calculation; ignores unreadable entries."""
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def precheck(
    release_size_bytes: int,
    install_dir: Path,
    meta: dict[str, Any],
    *,
    staging_root: Path = Path("/var/lib/selena/update/staging"),
) -> tuple[bool, str]:
    """Validate the host can accept this update before downloading.

    Returns ``(ok, reason)``. On success ``reason`` is an empty string.
    Checks: Python version, install lock, update-in-progress flag, free disk.
    """
    min_python = _parse_python_version(str(meta.get("min_python", "3.11")))
    if sys.version_info[:2] < min_python:
        return (
            False,
            f"requires Python >= {min_python[0]}.{min_python[1]}, "
            f"have {sys.version_info.major}.{sys.version_info.minor}",
        )

    if INSTALL_LOCK_PATH.exists():
        return False, f"install lock present: {INSTALL_LOCK_PATH}"

    if UPDATE_FLAG_PATH.exists():
        return False, f"update flag present: {UPDATE_FLAG_PATH}"

    # Free space on the partition that will hold staging + backup.
    # Heuristic: tarball download (1×) + extracted staging (~3×) +
    # snapshot of install dir for hardlink-aware backup, plus headroom.
    safety = 100 * 1024 * 1024  # 100 MB
    install_size = _dir_size_bytes(install_dir) if install_dir.exists() else 0
    needed = release_size_bytes * 4 + install_size + safety

    target = staging_root if staging_root.exists() else staging_root.parent
    target = target if target.exists() else Path("/var/lib")
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return False, f"disk_usage check failed: {exc}"

    if usage.free < needed:
        missing_mb = (needed - usage.free) // (1024 * 1024)
        return False, f"not enough disk space: missing ~{missing_mb} MB on {target}"

    return True, ""


def acquire_install_lock(tag: str) -> bool:
    """Try to claim the on-disk install lock; returns True on success.

    The lock survives core restarts: if a previous install crashed without
    cleanup, the operator must remove the file manually. We do not auto-clear
    here because a stale lock may indicate a real in-flight update.
    """
    try:
        INSTALL_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL via 'x' mode — atomic create-or-fail.
        with open(INSTALL_LOCK_PATH, "x") as fh:
            fh.write(tag)
        return True
    except FileExistsError:
        return False
    except OSError as exc:
        logger.warning("could not acquire install lock: %s", exc)
        return False


def release_install_lock() -> None:
    try:
        INSTALL_LOCK_PATH.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("could not release install lock: %s", exc)


def dispatch_external(
    tag: str,
    action: str = "install",
    *,
    on_active_sec: int = 1,
    apply_script: str = APPLY_UPDATE_SCRIPT,
    use_sudo: bool = True,
) -> str:
    """Dispatch ``apply-update.sh`` in a transient systemd unit.

    Returns the unit name. Does NOT wait for the unit to finish — by the time
    this returns, smarthome-core is about to be stopped by the script.
    """
    if action not in ("install", "rollback"):
        raise ValueError(f"invalid action: {action!r}")

    unit_name = f"selena-update-{sanitize_unit_tag(tag)}"
    cmd: list[str] = []
    if use_sudo:
        cmd.append("sudo")
    cmd.extend([
        "systemd-run",
        f"--on-active={on_active_sec}",
        f"--unit={unit_name}",
        "--no-block",
        apply_script,
        tag,
        action,
    ])

    logger.info("dispatching update: %s", " ".join(cmd))
    # shell=False, fixed argv — tag has already been sanitized by sudoers
    # glob ([a-zA-Z0-9._-]*), but we forward the original tag here so the
    # script sees the exact string for logging and .version writing.
    subprocess.run(cmd, check=True, shell=False)
    return unit_name


def last_apply_result(
    tag: str | None = None,
    log_path: Path = DEFAULT_LOG_PATH,
    max_lines: int = 200,
) -> str:
    """Tail the apply-update log; ``tag`` filters to that tag's session."""
    try:
        text = log_path.read_text()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        logger.warning("could not read apply log: %s", exc)
        return ""

    lines = text.splitlines()
    if tag:
        # Find the latest "=== install/rollback <tag> START ===" marker and
        # return everything from that point.
        marker = f" {tag} START"
        starts = [i for i, ln in enumerate(lines) if marker in ln]
        if starts:
            lines = lines[starts[-1] :]
    return "\n".join(lines[-max_lines:])


async def wait_for_external_completion(
    unit_name: str, timeout_sec: int = 600
) -> str:
    """Best-effort wait for a transient unit to leave the active state.

    Used by tests — production flow does not call this because by the time
    the unit transitions, smarthome-core has been stopped.
    """
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while True:
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl",
                "is-active",
                unit_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
        except FileNotFoundError:
            return "no-systemctl"

        state = (stdout or b"").decode().strip()
        if state in ("inactive", "failed"):
            return state
        if asyncio.get_event_loop().time() >= deadline:
            return f"timeout:{state}"
        await asyncio.sleep(2)
