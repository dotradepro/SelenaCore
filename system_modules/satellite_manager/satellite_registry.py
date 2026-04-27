"""Satellite device CRUD over the core Device registry.

Thin adapter that speaks to the same `devices` table every other module uses,
but scoped to satellite-manager's `module_id` so we don't step on other
modules' rows. Direct ORM is used here (rather than DeviceRegistry) because
we need control over the primary key (`sat_<mac>`) and `module_id`, neither
of which the generic DeviceRegistry.create exposes.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from core.registry.models import Device

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)

MODULE_ID = "satellite-manager"
ENTITY_TYPE = "satellite_speaker"
DEVICE_TYPE = "speaker"
PROTOCOL = "selena_satellite"


def device_id_for_mac(mac: str) -> str:
    return "sat_" + mac.replace(":", "").replace("-", "").lower()


class SatelliteRegistry:
    """CRUD helpers for satellite_speaker rows in the core Device table."""

    def __init__(self, session_factory: "async_sessionmaker") -> None:
        self._sf = session_factory

    async def register(
        self,
        mac: str,
        firmware: str,
        hardware: str,
        capabilities: list[str],
        ip: str | None = None,
    ) -> dict:
        """Idempotently register a satellite. Re-registration updates IP + firmware
        and marks the device online, but preserves location/name if already set.

        Returns {device_id, location} of the resulting row.
        """
        device_id = device_id_for_mac(mac)
        now_iso = datetime.now(timezone.utc).isoformat()

        async with self._sf() as session:
            existing = await session.get(Device, device_id)
            if existing:
                meta = json.loads(existing.meta or "{}")
                meta.update({"ip": ip, "firmware": firmware, "last_registered_at": now_iso})
                existing.meta = json.dumps(meta)
                state = json.loads(existing.state or "{}")
                state["online"] = True
                existing.state = json.dumps(state)
                await session.commit()
                return {"device_id": device_id, "location": existing.location}

            device = Device(
                device_id=device_id,
                name=f"Satellite {mac[-5:]}",
                type=DEVICE_TYPE,
                entity_type=ENTITY_TYPE,
                module_id=MODULE_ID,
                protocol=PROTOCOL,
                location=None,
            )
            device.set_capabilities(capabilities)
            device.set_meta({
                "mac": mac,
                "firmware": firmware,
                "hardware": hardware,
                "ip": ip,
                "registered_at": now_iso,
            })
            device.set_state({
                "online": True,
                "volume": 75,
                "muted": False,
                "rssi": 0,
            })
            session.add(device)
            await session.commit()
            logger.info("Satellite registered: %s (mac=%s, ip=%s)", device_id, mac, ip)
            return {"device_id": device_id, "location": None}

    async def get(self, device_id: str) -> dict | None:
        async with self._sf() as session:
            device = await session.get(Device, device_id)
            if not device or device.module_id != MODULE_ID:
                return None
            return _to_dict(device)

    async def list_all(self) -> list[dict]:
        async with self._sf() as session:
            stmt = select(Device).where(
                Device.module_id == MODULE_ID,
                Device.entity_type == ENTITY_TYPE,
            )
            rows = list((await session.execute(stmt)).scalars())
            return [_to_dict(r) for r in rows]

    async def update(self, device_id: str, **fields: Any) -> bool:
        """Update satellite fields. Accepts: name, location, volume, muted."""
        async with self._sf() as session:
            device = await session.get(Device, device_id)
            if not device or device.module_id != MODULE_ID:
                return False
            if "name" in fields:
                device.name = fields["name"]
            if "location" in fields:
                device.location = fields["location"]
            state_updates: dict[str, Any] = {}
            for key in ("volume", "muted"):
                if key in fields:
                    state_updates[key] = fields[key]
            if state_updates:
                state = json.loads(device.state or "{}")
                state.update(state_updates)
                device.state = json.dumps(state)
            await session.commit()
            return True

    async def set_online(self, device_id: str, online: bool) -> None:
        async with self._sf() as session:
            device = await session.get(Device, device_id)
            if not device or device.module_id != MODULE_ID:
                return
            state = json.loads(device.state or "{}")
            state["online"] = online
            device.state = json.dumps(state)
            if online:
                device.last_seen = datetime.now(timezone.utc)
            await session.commit()

    async def update_state(self, device_id: str, updates: dict) -> None:
        """Partial merge into Device.state JSON (rssi, volume, muted, etc.)."""
        async with self._sf() as session:
            device = await session.get(Device, device_id)
            if not device or device.module_id != MODULE_ID:
                return
            state = json.loads(device.state or "{}")
            state.update(updates)
            device.state = json.dumps(state)
            device.last_seen = datetime.now(timezone.utc)
            await session.commit()

    async def delete(self, device_id: str) -> bool:
        async with self._sf() as session:
            device = await session.get(Device, device_id)
            if not device or device.module_id != MODULE_ID:
                return False
            await session.delete(device)
            await session.commit()
            logger.info("Satellite deleted: %s", device_id)
            return True


def _to_dict(device: Device) -> dict:
    return {
        "device_id": device.device_id,
        "name": device.name,
        "location": device.location,
        "enabled": device.enabled,
        "meta": json.loads(device.meta or "{}"),
        "state": json.loads(device.state or "{}"),
        "capabilities": json.loads(device.capabilities or "[]"),
    }
