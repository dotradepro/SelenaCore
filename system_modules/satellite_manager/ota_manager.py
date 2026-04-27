"""OTA firmware manager — v1 STUB.

Endpoint surface exists so firmware authors and UI can start consuming it,
but uploads are rejected with 501 until real signing + versioning lands.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class OTAManager:
    async def upload(self, body: dict) -> dict:
        from fastapi import HTTPException
        raise HTTPException(status_code=501, detail="OTA upload not implemented in v1")

    def latest(self) -> dict:
        return {"version": None, "url": None}
