"""
core/api/routes/integrity.py — Integrity status endpoint

Reads shared state from the Integrity Agent via a JSON file written
by the agent process at /var/lib/selena/integrity_state.json.
Falls back to in-memory defaults if the file is unavailable.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from core.api.auth import verify_module_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrity", tags=["integrity"])

_STATE_FILE = Path(
    os.environ.get("CORE_DATA_DIR", "/var/lib/selena")
) / "integrity_state.json"

_DEFAULT_STATE: dict[str, Any] = {
    "status": "ok",
    "last_check": None,
    "check_interval_sec": 30,
    "changed_files": [],
    "restore_attempts": 0,
    "safe_mode_since": None,
}

# In-memory state (updated from file or directly)
_integrity_state: dict[str, Any] = dict(_DEFAULT_STATE)


def update_integrity_state(update: dict[str, Any]) -> None:
    _integrity_state.update(update)


def _read_agent_state() -> dict[str, Any]:
    """Read integrity state written by the agent process."""
    if not _STATE_FILE.exists():
        return _integrity_state
    try:
        data = json.loads(_STATE_FILE.read_text())
        _integrity_state.update(data)
    except Exception as e:
        logger.debug("Could not read agent state file: %s", e)
    return _integrity_state


@router.get("/status")
async def integrity_status(
    _module_id: str = Depends(verify_module_token),
) -> dict[str, Any]:
    return _read_agent_state()
