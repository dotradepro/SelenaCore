"""
system_modules/lights_switches/module.py — LightsSwitchesModule.

High-level UI module for every device with entity_type ∈
{light, switch, outlet}. Mirrors the climate module pattern: subscribes
to device.state_changed for cache freshness, forwards commands to
device-control via in-process Python call, owns NO voice intents.

Voice control for these devices stays in device-control's existing
device.on / device.off handlers — no pattern crossover.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from core.module_loader.system_module import SystemModule

logger = logging.getLogger(__name__)

MODULE_DIR = Path(__file__).parent

#: entity_types this module renders / commands.
LIGHTS_ENTITY_TYPES: tuple[str, ...] = ("light", "switch", "outlet")


class LightsSwitchesModule(SystemModule):
    name = "lights-switches"

    def __init__(self) -> None:
        super().__init__()
        self._latest: dict[str, dict[str, Any]] = {}
        self._watts: dict[str, float] = {}
        self._dc: Any = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.subscribe(["device.state_changed"], self._on_state_event)
        self.subscribe(["device.power_reading"], self._on_power_event)
        self.subscribe(["device.registered", "device.removed"], self._on_device_lifecycle)
        logger.info("LightsSwitchesModule started")

    async def stop(self) -> None:
        self._cleanup_subscriptions()
        self._latest.clear()
        self._watts.clear()
        self._dc = None
        logger.info("LightsSwitchesModule stopped")

    # ── Router ───────────────────────────────────────────────────────────

    def get_router(self) -> APIRouter:
        from .routes import build_router
        router = build_router(self)
        self._register_html_routes(router, __file__)
        return router

    # ── Cross-module helper ──────────────────────────────────────────────

    def _device_control(self) -> Any | None:
        if self._dc is not None:
            return self._dc
        try:
            from core.module_loader.sandbox import get_sandbox
            self._dc = get_sandbox().get_in_process_module("device-control")
        except Exception as exc:
            logger.warning("lights-switches: failed to resolve device-control: %s", exc)
            return None
        if self._dc is None:
            logger.warning("lights-switches: device-control module not loaded")
        return self._dc

    # ── EventBus handlers ────────────────────────────────────────────────

    async def _on_state_event(self, event: Any) -> None:
        try:
            payload = event.payload or {}
            device_id = payload.get("device_id")
            if not device_id:
                return
            new_state = payload.get("new_state")
            if not isinstance(new_state, dict):
                return
            cached = self._latest.get(device_id) or {}
            cached.update(new_state)
            self._latest[device_id] = cached
        except Exception as exc:
            logger.exception("lights-switches: state-event handler crashed: %s", exc)

    async def _on_power_event(self, event: Any) -> None:
        try:
            payload = event.payload or {}
            device_id = payload.get("device_id")
            watts = payload.get("watts")
            if device_id and watts is not None:
                self._watts[device_id] = float(watts)
        except Exception as exc:
            logger.exception("lights-switches: power-event handler crashed: %s", exc)

    async def _on_device_lifecycle(self, event: Any) -> None:
        try:
            payload = event.payload or {}
            entity_type = (payload.get("entity_type") or "").lower()
            if entity_type and entity_type not in LIGHTS_ENTITY_TYPES:
                return
            device_id = payload.get("device_id")
            if event.type == "device.removed" and device_id:
                self._latest.pop(device_id, None)
                self._watts.pop(device_id, None)
        except Exception as exc:
            logger.exception("lights-switches: lifecycle handler crashed: %s", exc)

    # ── Public API used by routes.py ─────────────────────────────────────

    async def list_devices(self) -> list[dict[str, Any]]:
        from sqlalchemy import select
        from core.registry.models import Device

        async with self._db_session() as session:
            stmt = (
                select(Device)
                .where(Device.entity_type.in_(LIGHTS_ENTITY_TYPES))
                .order_by(Device.location, Device.name)
            )
            rows = list((await session.execute(stmt)).scalars())

        out: list[dict[str, Any]] = []
        for d in rows:
            db_state = json.loads(d.state) if d.state else {}
            cached = self._latest.get(d.device_id) or {}
            merged = {**db_state, **cached}
            watts = self._watts.get(d.device_id)
            if watts is not None:
                merged["estimated_watts"] = watts
            out.append({
                "device_id": d.device_id,
                "name": d.name,
                "location": d.location or "",
                "entity_type": d.entity_type,
                "protocol": d.protocol,
                "enabled": bool(d.enabled),
                "state": merged,
                "capabilities": json.loads(d.capabilities) if d.capabilities else [],
                "last_seen": d.last_seen.timestamp() if d.last_seen else None,
            })
        return out

    async def apply_command(
        self, device_id: str, state: dict[str, Any],
    ) -> dict[str, Any]:
        dc = self._device_control()
        if dc is None:
            raise RuntimeError(
                "device-control module is not loaded — lights-switches cannot dispatch"
            )
        await dc.execute_command(device_id, state)
        cached = self._latest.get(device_id) or {}
        cached.update(state)
        self._latest[device_id] = cached
        return cached
