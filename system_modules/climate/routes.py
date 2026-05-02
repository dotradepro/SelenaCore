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
    """Body for ``POST /widget/action/{set_mode|step}`` (Dashboard V2).

    ``device_id`` is the multi-room carousel hook: when the widget is
    showing room N out of M, the action targets that room's device. Old
    single-device clients omit it and fall back to the primary device.
    """
    id: str
    value: float | None = None
    device_id: str | None = None


class WidgetSetStateBody(BaseModel):
    """Body for ``POST /widget/action/set_state`` — accepts any subset
    of the device's writable state keys (power on/off, fan_speed, swing,
    sleep/turbo/quiet/eco/health/light flags, etc.). Validated against
    :data:`ALLOWED_STATE_KEYS` so the widget can't push arbitrary keys
    that the driver doesn't understand."""
    patch: dict[str, Any]
    device_id: str | None = None


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
    # Multi-room shape: when there are multiple climate devices, the
    # response carries the alphabetically-first room as the top-level
    # payload AND a `rooms: ControlPanelRoomPayload[]` array with every
    # room (including primary) so the frontend ControlPanel template can
    # render a carousel switcher. Single-device installs just get the
    # one room directly with no `rooms` field.

    MODE_OPTIONS = [
        {"id": "auto", "label": "Auto", "label_key": "widgets.climate.modeAuto"},
        {"id": "cool", "label": "Cool", "label_key": "widgets.climate.modeCool"},
        {"id": "heat", "label": "Heat", "label_key": "widgets.climate.modeHeat"},
        {"id": "dry",  "label": "Dry",  "label_key": "widgets.climate.modeDry"},
    ]

    # Fan-speed options match the canonical names accepted by the Gree
    # / generic climate driver layer (see ``drivers/gree.py``). We
    # collapse the 6-step Gree ladder (auto / low / medium_low / medium
    # / medium_high / high) to a 4-step UI (auto / low / medium / high)
    # — three buttons in the middle would be a usability mess on a
    # narrow widget cell, and "medium_low" / "medium_high" are rarely
    # the user's actual intent.
    FAN_SPEED_OPTIONS = [
        {"id": "auto",   "label": "Auto",   "label_key": "widgets.climate.fanAuto"},
        {"id": "low",    "label": "Low",    "label_key": "widgets.climate.fanLow"},
        {"id": "medium", "label": "Medium", "label_key": "widgets.climate.fanMed"},
        {"id": "high",   "label": "High",   "label_key": "widgets.climate.fanHigh"},
    ]

    #: Boolean flags advertised by Gree-class climate devices. Each entry
    #: is rendered as a toggle in the widget's "Modes" bank — only the
    #: flags actually present in ``state`` show up, so unsupported drivers
    #: get a smaller bank automatically.
    CLIMATE_FLAGS = [
        ("sleep",  "Sleep",  "widgets.climate.flagSleep"),
        ("turbo",  "Turbo",  "widgets.climate.flagTurbo"),
        ("quiet",  "Quiet",  "widgets.climate.flagQuiet"),
        ("eco",    "Eco",    "widgets.climate.flagEco"),
        ("health", "Health", "widgets.climate.flagHealth"),
        ("light",  "Light",  "widgets.climate.flagLight"),
    ]

    def _build_room_payload(device: dict[str, Any]) -> dict[str, Any]:
        """Build one ControlPanel-shaped payload for a single climate device."""
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

        # Read-only readouts only — fan_speed is now an editable selector
        # below the mode buttons, so it no longer doubles as a pill.
        secondary_pills: list[dict[str, Any]] = []
        if (humidity := state.get("humidity")) is not None:
            secondary_pills.append({"icon": "droplets", "value": f"{int(humidity)}%"})
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

        # Power toggle — separate from the mode bank because a device can
        # be off while still tracking a heat/cool mode (it'll resume that
        # mode when powered back on). The widget renders this as a
        # prominent on/off toggle in the header.
        power = {"on": bool(state.get("on"))}

        # Fan-speed selector. Render as segmented control alongside modes
        # when the driver advertises ``set_fan_speed``. Also gracefully
        # handles drivers that report the value but don't expose write
        # capability — frontend just doesn't fire the action. Drivers
        # report ladder positions like "medium_low" / "medium_high" that
        # we collapse onto the 4-step UI bucket so the segmented control
        # always has an "active" highlight.
        FAN_LADDER_TO_UI = {
            "auto": "auto",
            "low": "low",
            "medium_low": "low",
            "medium": "medium",
            "medium_high": "high",
            "high": "high",
        }
        fan_speed_block: dict[str, Any] | None = None
        if (raw_fan := state.get("fan_speed")) is not None:
            normalized = FAN_LADDER_TO_UI.get(str(raw_fan).lower(), "auto")
            fan_speed_block = {
                "current": normalized,
                "options": FAN_SPEED_OPTIONS,
            }

        # Boolean climate flags. One toggle pill per flag the driver
        # actually reports; absent flags are hidden, so a thermostat
        # that only has sleep/eco gets a 2-pill bank instead of 6.
        flags: list[dict[str, Any]] = []
        for flag_id, raw_label, label_key in CLIMATE_FLAGS:
            if flag_id not in state:
                continue
            flags.append({
                "id": flag_id,
                "label": raw_label,
                "label_key": label_key,
                "on": bool(state.get(flag_id)),
            })

        room_payload: dict[str, Any] = {
            "device_id": device["device_id"],
            "room": location or None,
            "label": label,
            "label_key": label_key,
            "power": power,
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
        if fan_speed_block:
            room_payload["fan_speed"] = fan_speed_block
        if flags:
            room_payload["flags"] = flags
        if label_args:
            room_payload["label_args"] = label_args
        return room_payload

    @router.get("/widget/data/state")
    async def widget_state() -> dict[str, Any]:
        devices = await svc.list_climate_devices()
        # Filter out disabled devices entirely — they can't be controlled.
        active = [d for d in devices if d.get("enabled", True)]
        if not active:
            raise HTTPException(503, "No climate device available")

        # Sort by location (alphabetically), with no-location devices last.
        # Primary (first slide of carousel, default for "All" tab) is the
        # alphabetically-first room. User picked this rule explicitly.
        sorted_devices = sorted(
            active,
            key=lambda d: ((d.get("location") or "￿").lower(), d.get("name", "")),
        )

        rooms = [_build_room_payload(d) for d in sorted_devices]
        primary = dict(rooms[0])  # Top-level mirrors first room
        if len(rooms) > 1:
            # Carousel: emit `rooms` array so the frontend renders a switcher.
            # The primary fields stay at top level for back-compat with any
            # client that doesn't yet know about the carousel.
            primary["rooms"] = rooms
        # Echo legacy `_device_id` for any existing client that still reads it.
        primary["_device_id"] = primary["device_id"]
        return primary

    async def _apply_to_device(
        device_id: str | None, state_patch: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply ``state_patch`` to a specific device, or to the primary
        when ``device_id`` is missing. The frontend carousel always sends
        the active slide's ``device_id``; older single-device clients omit
        it and fall through to the primary."""
        devices = await svc.list_climate_devices()
        active = [d for d in devices if d.get("enabled", True)]
        if not active:
            raise HTTPException(503, "No climate device available")

        target_device: dict[str, Any] | None = None
        if device_id:
            for d in active:
                if d.get("device_id") == device_id:
                    target_device = d
                    break
            if target_device is None:
                raise HTTPException(404, f"Device {device_id!r} not found")
        else:
            sorted_devs = sorted(
                active,
                key=lambda d: ((d.get("location") or "￿").lower(), d.get("name", "")),
            )
            target_device = sorted_devs[0]

        try:
            new_state = await svc.apply_command(target_device["device_id"], state_patch)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        except Exception as exc:
            logger.warning("climate widget command failed: %s", exc)
            raise HTTPException(502, f"Command failed: {exc}")
        return {"status": "ok", "device_id": target_device["device_id"], "state": new_state}

    @router.post("/widget/action/set_mode")
    async def widget_set_mode(body: WidgetActionBody) -> dict[str, Any]:
        if body.id not in {o["id"] for o in MODE_OPTIONS}:
            raise HTTPException(422, f"Unknown mode {body.id!r}")
        return await _apply_to_device(body.device_id, {"mode": body.id})

    @router.post("/widget/action/step")
    async def widget_step(body: WidgetActionBody) -> dict[str, Any]:
        if body.id != "temp" or body.value is None:
            raise HTTPException(422, "Stepper id must be 'temp' with a numeric value")
        clamped = max(16.0, min(30.0, float(body.value)))
        return await _apply_to_device(body.device_id, {"target_temp": clamped})

    @router.post("/widget/action/set_state")
    async def widget_set_state(body: WidgetSetStateBody) -> dict[str, Any]:
        """Generic state-patch action used by the widget for everything
        that doesn't fit the mode/step contract: power on/off, fan speed,
        boolean climate flags, swing direction. Validated against
        :data:`ALLOWED_STATE_KEYS` so a malicious / buggy frontend can't
        push keys the driver doesn't understand."""
        if not body.patch:
            raise HTTPException(422, "Empty state patch")
        unknown = set(body.patch) - ALLOWED_STATE_KEYS
        if unknown:
            raise HTTPException(422, f"Unsupported state keys: {sorted(unknown)}")
        return await _apply_to_device(body.device_id, body.patch)

    return router
