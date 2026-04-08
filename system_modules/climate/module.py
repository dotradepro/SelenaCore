"""
system_modules/climate/module.py — ClimateModule.

High-level UI/control surface for every device with entity_type ∈
{air_conditioner, thermostat}. Pure presentation layer:

* Lists devices grouped by room (location).
* Forwards commands to ``device-control.execute_command()`` via direct
  in-process call (both modules live in the same Python interpreter).
* Caches latest state per device from ``device.state_changed`` events for
  fast widget reads — no polling.

Climate module deliberately owns NO voice intents — that responsibility
sits in ``device-control`` so light/switch/AC intents share one resolver
and never overlap.
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

#: entity_types that this module renders / commands.
CLIMATE_ENTITY_TYPES: tuple[str, ...] = ("air_conditioner", "thermostat")


class ClimateModule(SystemModule):
    name = "climate"

    def __init__(self) -> None:
        super().__init__()
        # Cache of latest state per device_id, populated from EventBus events.
        self._latest: dict[str, dict[str, Any]] = {}
        # Cache of latest power reading per device_id (watts), populated from
        # device.power_reading events. Climate widgets show this inline so
        # the user can see live AC power draw without opening energy-monitor.
        self._watts: dict[str, float] = {}
        # Lazy reference to the device-control sibling module instance.
        self._dc: Any = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.subscribe(["device.state_changed"], self._on_state_event)
        self.subscribe(["device.power_reading"], self._on_power_event)
        logger.info("ClimateModule started")

    async def stop(self) -> None:
        self._cleanup_subscriptions()
        self._latest.clear()
        self._watts.clear()
        self._dc = None
        logger.info("ClimateModule stopped")

    # ── Router ───────────────────────────────────────────────────────────

    def get_router(self) -> APIRouter:
        from .routes import build_router
        router = build_router(self)
        self._register_html_routes(router, __file__)
        return router

    # ── Cross-module helper ──────────────────────────────────────────────

    def _device_control(self) -> Any | None:
        """Return the in-process device-control instance, or None.

        Cached after first lookup. ``None`` is treated as a temporary
        condition (device-control might not be loaded yet); callers should
        log a warning and bail.
        """
        if self._dc is not None:
            return self._dc
        try:
            from core.module_loader.sandbox import get_sandbox
            self._dc = get_sandbox().get_in_process_module("device-control")
        except Exception as exc:
            logger.warning("climate: failed to resolve device-control: %s", exc)
            return None
        if self._dc is None:
            logger.warning("climate: device-control module not loaded")
        return self._dc

    # ── EventBus handler ─────────────────────────────────────────────────

    async def _on_state_event(self, event: Any) -> None:
        try:
            payload = event.payload or {}
            device_id = payload.get("device_id")
            if not device_id:
                return
            new_state = payload.get("new_state")
            if not isinstance(new_state, dict):
                return
            # Only cache devices we actually render. We can't filter on
            # event payload alone (no entity_type field), so accept the
            # update — DB lookup at GET /devices time will reject non-
            # climate devices anyway.
            cached = self._latest.get(device_id) or {}
            cached.update(new_state)
            self._latest[device_id] = cached
        except Exception as exc:
            logger.exception("climate: state-event handler crashed: %s", exc)

    async def _on_power_event(self, event: Any) -> None:
        try:
            payload = event.payload or {}
            device_id = payload.get("device_id")
            watts = payload.get("watts")
            if device_id and watts is not None:
                self._watts[device_id] = float(watts)
        except Exception as exc:
            logger.exception("climate: power-event handler crashed: %s", exc)

    # ── Public API used by routes.py ─────────────────────────────────────

    async def list_climate_devices(self) -> list[dict[str, Any]]:
        """Return all climate devices grouped flatly with cached state."""
        from sqlalchemy import select
        from core.registry.models import Device

        async with self._db_session() as session:
            stmt = (
                select(Device)
                .where(Device.entity_type.in_(CLIMATE_ENTITY_TYPES))
                .order_by(Device.location, Device.name)
            )
            rows = list((await session.execute(stmt)).scalars())

        out: list[dict[str, Any]] = []
        for d in rows:
            db_state = json.loads(d.state) if d.state else {}
            cached = self._latest.get(d.device_id) or {}
            merged = {**db_state, **cached}
            # Inject the latest power reading (estimated by the driver)
            # so the widget can show live wattage without a separate call.
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
        """Forward a command dict to device-control's executor."""
        dc = self._device_control()
        if dc is None:
            raise RuntimeError(
                "device-control module is not loaded — climate cannot dispatch"
            )
        await dc.execute_command(device_id, state)
        # Optimistic local cache update.
        cached = self._latest.get(device_id) or {}
        cached.update(state)
        self._latest[device_id] = cached
        return cached
