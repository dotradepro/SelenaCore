"""
core/api/routes/system.py — health + system info endpoints
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])

_start_time = time.time()

# Mode is controlled by integrity agent state
_system_mode: str = "normal"
_integrity_status: str = "ok"


def set_system_mode(mode: str) -> None:
    global _system_mode
    _system_mode = mode


def set_integrity_status(status: str) -> None:
    global _integrity_status
    _integrity_status = status


def get_system_mode() -> str:
    return _system_mode


@router.get("/health")
async def health() -> dict[str, Any]:
    from core.version import VERSION
    return {
        "status": "ok",
        "version": VERSION,
        "mode": _system_mode,
        "uptime": int(time.time() - _start_time),
        "integrity": _integrity_status,
    }


_INSTALL_ID_CACHE: str | None = None


def _get_or_create_install_id() -> str:
    """Return a stable UUID identifying this SelenaCore deployment.

    Written once to ``$CORE_DATA_DIR/.install-id`` on first read. When the
    Docker volume is wiped (fresh install) the file disappears and a new
    UUID is generated. The frontend compares this value with its
    localStorage copy to detect stale wizard state after a re-install.

    The file lives inside the volume-mounted data directory so it's
    visible from the container (where this code runs) — NOT in the host
    /var/lib/selena, which is a separate namespace when using named
    Docker volumes.
    """
    global _INSTALL_ID_CACHE
    if _INSTALL_ID_CACHE:
        return _INSTALL_ID_CACHE
    import os
    import uuid
    data_dir = os.environ.get("CORE_DATA_DIR", "/var/lib/selena")
    id_path = os.path.join(data_dir, ".install-id")
    try:
        if os.path.exists(id_path):
            with open(id_path) as f:
                _INSTALL_ID_CACHE = f.read().strip() or None
        if not _INSTALL_ID_CACHE:
            os.makedirs(data_dir, exist_ok=True)
            _INSTALL_ID_CACHE = str(uuid.uuid4())
            with open(id_path, "w") as f:
                f.write(_INSTALL_ID_CACHE + "\n")
            logger.info("Generated install_id=%s at %s", _INSTALL_ID_CACHE, id_path)
    except Exception as exc:
        logger.debug("install-id handling failed: %s", exc)
        _INSTALL_ID_CACHE = _INSTALL_ID_CACHE or ""
    return _INSTALL_ID_CACHE or ""


def _detect_hdmi_connected() -> bool:
    """True if any /sys/class/drm connector reports status=connected.

    Works from inside the docker container because /sys is mounted by
    default. Matches install.sh HAS_DISPLAY detection.
    """
    from pathlib import Path
    try:
        for status_path in Path("/sys/class/drm").glob("*/status"):
            try:
                if status_path.read_text().strip() == "connected":
                    return True
            except OSError:
                continue
    except Exception:
        pass
    return False


@router.get("/system/info")
async def system_info() -> dict[str, Any]:
    import platform

    from core.config import get_yaml_config
    yaml_cfg = get_yaml_config()
    system_cfg = yaml_cfg.get("system", {})
    wizard_cfg = yaml_cfg.get("wizard", {})

    # Hardware info (basic — full impl in hw_monitor)
    try:
        import psutil
        ram_total_mb = psutil.virtual_memory().total // (1024 * 1024)
    except Exception:
        ram_total_mb = 0

    # Display / HDMI detection — delegates to ui_core module so both
    # the install.sh detection and the runtime API agree.
    display_mode = "headless"
    try:
        from system_modules.ui_core.display import detect_display_mode
        display_mode = detect_display_mode()
    except Exception as exc:
        logger.debug("display mode detection failed: %s", exc)

    install_id = _get_or_create_install_id()

    from core.version import VERSION
    return {
        "initialized": system_cfg.get("initialized", False),
        "wizard_completed": wizard_cfg.get("completed", False),
        "install_id": install_id,
        "version": VERSION,
        "hardware": {
            "model": platform.node(),
            "ram_total_mb": ram_total_mb,
            "has_hdmi": _detect_hdmi_connected(),
            "has_camera": False,
        },
        "audio": {
            "inputs": [],   # filled by voice_core
            "outputs": [],
        },
        "display_mode": display_mode,
    }
