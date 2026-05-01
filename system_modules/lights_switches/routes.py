"""
system_modules/lights_switches/routes.py — REST router.

Mounted at /api/ui/modules/lights-switches/.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.api.widget_helpers import entity_icon

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


class ToggleBody(BaseModel):
    """Body for ``POST /widget/action/toggle`` (Dashboard V2)."""
    id: str


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

    # ── Dashboard V2 template-engine endpoints ──────────────────────────────
    # Manifest declares these paths under ui.widget.data_endpoints / actions
    # so the V2 dashboard renders this module as a `toggle-list` template
    # instead of the legacy widget.html iframe. Icon mapping comes from the
    # shared `core.api.widget_helpers.ENTITY_ICON` table — see file header.

    @router.get("/widget/data/state")
    async def widget_state() -> dict[str, Any]:
        devices = await svc.list_devices()
        items = []
        on_count = 0
        for d in devices:
            if not d.get("enabled", True):
                continue
            state = d.get("state") or {}
            is_on = bool(state.get("on") or state.get("power") == "on")
            if is_on:
                on_count += 1
            secondary: str | None = None
            if "brightness" in state and state["brightness"] is not None:
                try:
                    pct = round(float(state["brightness"]) / 255 * 100)
                    secondary = f"{pct}%"
                except (TypeError, ValueError):
                    secondary = None
            items.append({
                "id": d["device_id"],
                "name": d["name"],
                "state": "on" if is_on else "off",
                "secondary": secondary,
                "icon": entity_icon(d.get("entity_type")),
                "location": d.get("location") or None,
            })
        # i18n: emit a translation key alongside the raw English string so the
        # frontend ToggleList template can localize via t(label_key) with the
        # raw `label` as defaultValue. Modules without keys keep working as
        # before — the template falls through to the raw string.
        if items:
            summary_key = "widgets.lightsSwitches.summarySomeOn"
            summary = f"{on_count} of {len(items)} on"
            summary_args = {"on": on_count, "total": len(items)}
        else:
            summary_key = "widgets.lightsSwitches.summaryEmpty"
            summary = "No lights"
            summary_args = None
        out: dict[str, Any] = {
            "label": "Lights",
            "label_key": "widgets.lightsSwitches.label",
            "summary": summary,
            "summary_key": summary_key,
            "items": items,
        }
        if summary_args is not None:
            out["summary_args"] = summary_args
        return out

    @router.post("/widget/action/toggle")
    async def widget_toggle(body: ToggleBody) -> dict[str, Any]:
        # Look up current state, flip the `on` bit. Mirrors apply_command
        # error handling so the proxy gets a deterministic 4xx/5xx surface.
        for d in await svc.list_devices():
            if d["device_id"] == body.id:
                cur = d.get("state") or {}
                next_on = not bool(cur.get("on") or cur.get("power") == "on")
                try:
                    await svc.apply_command(body.id, {"on": next_on})
                except RuntimeError as exc:
                    raise HTTPException(503, str(exc))
                return {"status": "ok", "id": body.id, "on": next_on}
        raise HTTPException(404, "Device not found")

    return router
