"""
core/api/routes/integrity.py — Integrity status endpoint
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from core.api.auth import verify_module_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrity", tags=["integrity"])

# State shared with integrity agent via IPC in full impl
_integrity_state: dict[str, Any] = {
    "status": "ok",
    "last_check": None,
    "check_interval_sec": 30,
    "changed_files": [],
    "restore_attempts": 0,
    "safe_mode_since": None,
}


def update_integrity_state(update: dict[str, Any]) -> None:
    _integrity_state.update(update)


@router.get("/status")
async def integrity_status(
    _token: str = Depends(verify_module_token),
) -> dict[str, Any]:
    return _integrity_state
