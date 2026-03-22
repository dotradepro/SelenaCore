"""
core/module_loader/system_module.py — Base class for in-process system modules.

System modules (type=SYSTEM) run INSIDE the smarthome-core container as Python
objects loaded via importlib — NOT as separate subprocesses or Docker containers.

Architecture:
  - SYSTEM modules  → importlib, no port, ~0 MB RAM overhead
  - User modules    → Docker sandbox container, port 8100-8200

Subclass contract:
  1. Set class attribute ``name`` matching manifest.json "name"
  2. Implement ``start()`` and ``stop()``
  3. Optionally implement ``get_router()`` → APIRouter mounted at
     /api/ui/modules/{name}/
  4. In __init__.py: export ``module_class = <YourClass>``
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable

if TYPE_CHECKING:
    from fastapi import APIRouter
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from core.eventbus.bus import EventBus

logger = logging.getLogger(__name__)


class SystemModule(ABC):
    """Base class for SYSTEM-type modules — runs inside core process.

    Do NOT launch system modules as uvicorn subprocesses.
    Do NOT specify ``port`` in their manifest.json.
    Use ``self.publish()``, ``self.subscribe()``, ``self.fetch_devices()``, etc.
    for all communication with core instead of HTTP calls.
    """

    name: str  # Must match manifest.json "name", e.g. "weather-service"

    def __init__(self) -> None:
        self._bus: "EventBus | None" = None
        self._session_factory: "async_sessionmaker | None" = None
        self._direct_sub_ids: list[str] = []

    def setup(self, bus: "EventBus", session_factory: "async_sessionmaker") -> None:
        """Inject core services. Called by loader before start()."""
        self._bus = bus
        self._session_factory = session_factory

    @abstractmethod
    async def start(self) -> None:
        """Start the module: initialize service, subscribe to events."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the module: cancel background tasks, release resources."""
        ...

    def get_router(self) -> "APIRouter | None":
        """Return a FastAPI APIRouter mounted at /api/ui/modules/{name}/.

        Override this method to expose REST endpoints.
        The router is mounted by the Plugin Manager right after startup.
        """
        return None

    # ── EventBus helpers ─────────────────────────────────────────────────────

    def subscribe(self, event_types: list[str], callback: Callable) -> str:
        """Subscribe to EventBus events with a direct async Python callback.

        The callback signature must be: ``async def handler(event: Event) -> None``
        Returns the subscription ID.
        """
        if self._bus is None:
            raise RuntimeError(
                f"Module '{self.name}': setup() must be called before subscribe()"
            )
        sub_id = self._bus.subscribe_direct(self.name, event_types, callback)
        self._direct_sub_ids.append(sub_id)
        return sub_id

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish an event to the EventBus."""
        if self._bus is None:
            raise RuntimeError(
                f"Module '{self.name}': setup() must be called before publish()"
            )
        await self._bus.publish(type=event_type, source=self.name, payload=payload)

    # ── DeviceRegistry helpers ────────────────────────────────────────────────

    @asynccontextmanager
    async def _db_session(self) -> "AsyncGenerator[AsyncSession, None]":
        """Context manager yielding a fresh SQLAlchemy session."""
        if self._session_factory is None:
            raise RuntimeError(
                f"Module '{self.name}': setup() must be called first"
            )
        async with self._session_factory() as session:
            yield session

    async def fetch_devices(self) -> list[dict[str, Any]]:
        """Return all registered devices as plain dicts."""
        from core.registry.service import DeviceRegistry

        async with self._db_session() as session:
            registry = DeviceRegistry(session)
            devices = await registry.get_all()
            return [_device_to_dict(d) for d in devices]

    async def patch_device_state(self, device_id: str, state: dict[str, Any]) -> None:
        """Update a device's state in the registry and commit."""
        from core.registry.service import DeviceRegistry

        async with self._db_session() as session:
            registry = DeviceRegistry(session)
            await registry.update_state(device_id, state)
            await session.commit()

    async def get_device_state(self, device_id: str) -> dict[str, Any]:
        """Return the state dict of a single device."""
        from core.registry.service import DeviceRegistry

        async with self._db_session() as session:
            registry = DeviceRegistry(session)
            device = await registry.get(device_id)
            if device is None:
                return {}
            return json.loads(device.state)

    async def register_device(
        self,
        name: str,
        type: str,
        protocol: str,
        capabilities: list[str],
        meta: dict[str, Any],
    ) -> str:
        """Register a new device and return its device_id."""
        from core.registry.service import DeviceRegistry

        async with self._db_session() as session:
            registry = DeviceRegistry(session)
            device = await registry.create(
                name=name,
                type=type,
                protocol=protocol,
                capabilities=capabilities,
                meta=meta,
            )
            await session.commit()
            return device.device_id

    # ── Internal ──────────────────────────────────────────────────────────────

    def _cleanup_subscriptions(self) -> None:
        """Unsubscribe all direct EventBus subscriptions."""
        if self._bus:
            for sub_id in self._direct_sub_ids:
                self._bus.unsubscribe_direct(sub_id)
        self._direct_sub_ids.clear()


def _device_to_dict(device: Any) -> dict[str, Any]:
    """Convert a Device ORM object to a plain dict (no SQLAlchemy state)."""
    return {
        "device_id": device.device_id,
        "name": device.name,
        "type": device.type,
        "protocol": device.protocol,
        "state": json.loads(device.state),
        "capabilities": json.loads(device.capabilities),
        "last_seen": device.last_seen.timestamp() if device.last_seen else None,
        "module_id": device.module_id,
        "meta": json.loads(device.meta),
    }
