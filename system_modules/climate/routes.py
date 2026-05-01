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


class WidgetActionBody(BaseModel):
    """Body for ``POST /widget/action/{set_mode|step}`` (Dashboard V2)."""
    id: str
    value: float | None = None


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

    # ── Dashboard V2 control-panel endpoint ─────────────────────────────────
    # Renders the first climate device the registry returns. Multi-room
    # composition (siblings array) is open question §1 of the recraft doc —
    # we lean toward extending this payload with an optional `siblings` field
    # in a later phase rather than spawning a per-room widget.

    MODE_OPTIONS = [
        {"id": "auto", "label": "Auto", "label_key": "widgets.climate.modeAuto"},
        {"id": "cool", "label": "Cool", "label_key": "widgets.climate.modeCool"},
        {"id": "heat", "label": "Heat", "label_key": "widgets.climate.modeHeat"},
        {"id": "dry",  "label": "Dry",  "label_key": "widgets.climate.modeDry"},
    ]

    def _primary_device(devices: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not devices:
            return None
        # Prefer an enabled, on-state device; otherwise first.
        for d in devices:
            if d.get("enabled") and (d.get("state") or {}).get("on"):
                return d
        for d in devices:
            if d.get("enabled"):
                return d
        return devices[0]

    @router.get("/widget/data/state")
    async def widget_state() -> dict[str, Any]:
        devices = await svc.list_climate_devices()
        device = _primary_device(devices)
        if device is None:
            raise HTTPException(503, "No climate device available")
        state = device.get("state") or {}
        target = state.get("target_temp")
        current = state.get("current_temp") or state.get("temperature") or target or 0
        mode = (state.get("mode") or "auto").lower()
        if isinstance(target, (int, float)):
            secondary: str | None = f"→ set {target:g}°"
            secondary_key: str | None = "widgets.climate.secondarySetpoint"
            secondary_args: dict[str, Any] | None = {"temp": f"{target:g}"}
        else:
            secondary = None
            secondary_key = None
            secondary_args = None
        location = device.get("location") or ""

        secondary_pills: list[dict[str, Any]] = []
        if (humidity := state.get("humidity")) is not None:
            secondary_pills.append({"icon": "droplets", "value": f"{int(humidity)}%"})
        if (fan := state.get("fan_speed")) is not None:
            secondary_pills.append({"icon": "wind", "value": str(fan).title()})
        if (watts := state.get("estimated_watts")) is not None and watts:
            secondary_pills.append({"icon": "zap", "value": f"{watts:.0f} W"})

        primary: dict[str, Any] = {
            "value": f"{float(current):.1f}",
            "unit": "°",
            "secondary": secondary,
        }
        if secondary_key:
            primary["secondary_key"] = secondary_key
            primary["secondary_args"] = secondary_args

        if location:
            label = f"Climate · {location}"
            label_key = "widgets.climate.labelLocation"
            label_args: dict[str, Any] | None = {"location": location}
        else:
            label = "Climate"
            label_key = "widgets.climate.label"
            label_args = None

        out: dict[str, Any] = {
            "_device_id": device["device_id"],  # echoed back in actions
            "label": label,
            "label_key": label_key,
            "primary": primary,
            "modes": {
                "current": mode if mode in {o["id"] for o in MODE_OPTIONS} else "auto",
                "options": MODE_OPTIONS,
            },
            "steppers": [
                {
                    "id": "temp",
                    "label": "Temp",
                    "label_key": "widgets.climate.stepperTemp",
                    "value": f"{float(target):.1f}" if isinstance(target, (int, float)) else "—",
                    "unit": "°",
                    "min": 16, "max": 30, "step": 0.5,
                }
            ] if isinstance(target, (int, float)) else [],
            "secondary_pills": secondary_pills,
        }
        if label_args:
            out["label_args"] = label_args
        return out

    async def _apply_to_primary(state_patch: dict[str, Any]) -> dict[str, Any]:
        devices = await svc.list_climate_devices()
        device = _primary_device(devices)
        if device is None:
            raise HTTPException(503, "No climate device available")
        try:
            new_state = await svc.apply_command(device["device_id"], state_patch)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        except Exception as exc:
            logger.warning("climate widget command failed: %s", exc)
            raise HTTPException(502, f"Command failed: {exc}")
        return {"status": "ok", "device_id": device["device_id"], "state": new_state}

    @router.post("/widget/action/set_mode")
    async def widget_set_mode(body: WidgetActionBody) -> dict[str, Any]:
        if body.id not in {o["id"] for o in MODE_OPTIONS}:
            raise HTTPException(422, f"Unknown mode {body.id!r}")
        return await _apply_to_primary({"mode": body.id})

    @router.post("/widget/action/step")
    async def widget_step(body: WidgetActionBody) -> dict[str, Any]:
        if body.id != "temp" or body.value is None:
            raise HTTPException(422, "Stepper id must be 'temp' with a numeric value")
        clamped = max(16.0, min(30.0, float(body.value)))
        return await _apply_to_primary({"target_temp": clamped})

    return router
