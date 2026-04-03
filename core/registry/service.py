"""
core/registry/service.py — DeviceRegistry service
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.registry.models import Device, StateHistory

logger = logging.getLogger(__name__)

# Max state history records per device
STATE_HISTORY_LIMIT = 1000


class DeviceNotFoundError(Exception):
    pass


class DeviceRegistry:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_all(self) -> list[Device]:
        result = await self._session.execute(select(Device))
        return list(result.scalars().all())

    async def get(self, device_id: str) -> Device | None:
        result = await self._session.execute(
            select(Device).where(Device.device_id == device_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        name: str,
        type: str,
        protocol: str,
        capabilities: list[str],
        meta: dict,
        keywords_user: list[str] | None = None,
        keywords_en: list[str] | None = None,
        entity_type: str | None = None,
        location: str | None = None,
    ) -> Device:
        device = Device(name=name, type=type, protocol=protocol)
        device.set_capabilities(capabilities)
        device.set_meta(meta)
        if keywords_user:
            device.set_keywords_user(keywords_user)
        if keywords_en:
            device.set_keywords_en(keywords_en)
        if entity_type:
            device.entity_type = entity_type
        if location:
            device.location = location
        self._session.add(device)
        await self._session.flush()
        logger.info("Device created: %s (%s)", device.device_id, name)
        return device

    async def update_state(self, device_id: str, new_state: dict) -> Device:
        device = await self.get(device_id)
        if device is None:
            raise DeviceNotFoundError(f"Device {device_id} not found")

        old_state = device.get_state()

        # Record history
        history_entry = StateHistory(
            device_id=device_id,
            old_state=device.state,
        )
        history_entry.new_state = __import__("json").dumps(new_state)
        self._session.add(history_entry)

        device.set_state(new_state)
        from datetime import datetime, timezone
        device.last_seen = datetime.now(timezone.utc)

        await self._session.flush()

        # Trim history to last STATE_HISTORY_LIMIT records
        await self._trim_history(device_id)

        logger.info(
            "Device state updated: %s | old=%s new=%s",
            device_id,
            old_state,
            new_state,
        )
        return device

    async def delete(self, device_id: str) -> None:
        device = await self.get(device_id)
        if device is None:
            raise DeviceNotFoundError(f"Device {device_id} not found")
        await self._session.delete(device)
        await self._session.flush()
        logger.info("Device deleted: %s", device_id)

    async def query(
        self,
        entity_type: str | None = None,
        location: str | None = None,
        keyword: str | None = None,
    ) -> list[Device]:
        """Search devices by entity_type, location, and/or keyword.

        Filters are AND-combined. keyword searches in name, keywords_en (JSON).
        """
        stmt = select(Device)
        if entity_type:
            stmt = stmt.where(Device.entity_type == entity_type)
        if location:
            stmt = stmt.where(Device.location == location)
        if keyword:
            kw_lower = f"%{keyword.lower()}%"
            stmt = stmt.where(
                Device.name.ilike(kw_lower)
                | Device.keywords_en.ilike(kw_lower)
            )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _trim_history(self, device_id: str) -> None:
        """Keep only the last STATE_HISTORY_LIMIT records for a device."""
        result = await self._session.execute(
            select(StateHistory.id)
            .where(StateHistory.device_id == device_id)
            .order_by(StateHistory.changed_at.desc())
            .offset(STATE_HISTORY_LIMIT)
        )
        old_ids = list(result.scalars().all())
        if old_ids:
            await self._session.execute(
                delete(StateHistory).where(StateHistory.id.in_(old_ids))
            )
