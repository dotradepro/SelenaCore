"""
system_modules/lights_switches/routes.py — REST router.

Mounted at /api/ui/modules/lights-switches/.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    from .module import LightsSwitchesModule

logger = logging.getLogger(__name__)

#: Logical state keys the widget is allowed to push to a device.
ALLOWED_STATE_KEYS: set[str] = {
    "on",
    "brightness",
    "colour_temp",
    "rgb_color",
}


class CommandBody(BaseModel):
    state: dict[str, Any]


def build_router(svc: "LightsSwitchesModule") -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "module": svc.name,
            "cached_devices": len(svc._latest),
        }

    @router.get("/devices")
    async def list_devices() -> dict[str, Any]:
        return {"devices": await svc.list_devices()}

    @router.get("/rooms")
    async def list_rooms() -> dict[str, Any]:
        devices = await svc.list_devices()
        rooms: dict[str, list[dict[str, Any]]] = {}
        for d in devices:
            room = d.get("location") or "unassigned"
            rooms.setdefault(room, []).append(d)
        return {"rooms": rooms}

    @router.get("/device/{device_id}")
    async def get_device(device_id: str) -> dict[str, Any]:
        for d in await svc.list_devices():
            if d["device_id"] == device_id:
                return d
        raise HTTPException(404, "Device not found")

    @router.post("/device/{device_id}/command")
    async def send_command(device_id: str, body: CommandBody) -> dict[str, Any]:
        bad = set(body.state.keys()) - ALLOWED_STATE_KEYS
        if bad:
            raise HTTPException(422, f"Unsupported state keys: {sorted(bad)}")
        try:
            new_state = await svc.apply_command(device_id, body.state)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        except Exception as exc:
            logger.warning(
                "lights-switches: command failed for %s: %s", device_id, exc,
            )
            raise HTTPException(502, f"Command failed: {exc}")
        return {"status": "ok", "state": new_state}

    return router
