"""
system_modules/climate/routes.py — REST router for the climate module.

Mounted by core at ``/api/ui/modules/climate/``.

Endpoints:
    GET  /devices               — flat list of every climate device
    GET  /rooms                 — same data, grouped by location
    GET  /device/{id}           — single device detail
    POST /device/{id}/command   — apply a state update (forwarded to device-control)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    from .module import ClimateModule

logger = logging.getLogger(__name__)

#: Logical state keys the climate widget is allowed to push to a device.
ALLOWED_STATE_KEYS: set[str] = {
    "on",
    "mode",
    "target_temp",
    "fan_speed",
    "swing_v",
    "swing_h",
    "sleep",
    "turbo",
    "light",
    "eco",
    "health",
    "quiet",
}


class CommandBody(BaseModel):
    state: dict[str, Any]


def build_router(svc: "ClimateModule") -> APIRouter:
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
        return {"devices": await svc.list_climate_devices()}

    @router.get("/rooms")
    async def list_rooms() -> dict[str, Any]:
        """Group climate devices by their ``location`` field."""
        devices = await svc.list_climate_devices()
        rooms: dict[str, list[dict[str, Any]]] = {}
        for d in devices:
            room = d.get("location") or "unassigned"
            rooms.setdefault(room, []).append(d)
        return {"rooms": rooms}

    @router.get("/device/{device_id}")
    async def get_device(device_id: str) -> dict[str, Any]:
        for d in await svc.list_climate_devices():
            if d["device_id"] == device_id:
                return d
        raise HTTPException(404, "Climate device not found")

    @router.post("/device/{device_id}/command")
    async def send_command(device_id: str, body: CommandBody) -> dict[str, Any]:
        # Validate keys before reaching device-control to give the UI a
        # crisp 422 instead of a generic driver error.
        bad = set(body.state.keys()) - ALLOWED_STATE_KEYS
        if bad:
            raise HTTPException(422, f"Unsupported state keys: {sorted(bad)}")
        try:
            new_state = await svc.apply_command(device_id, body.state)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        except Exception as exc:
            logger.warning(
                "climate: command failed for %s: %s", device_id, exc,
            )
            raise HTTPException(502, f"Command failed: {exc}")
        return {"status": "ok", "state": new_state}

    return router
