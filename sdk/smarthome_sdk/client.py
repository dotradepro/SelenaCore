"""
sdk/smarthome_sdk/client.py — Core API client for user modules

Provides a typed HTTP client for interacting with the SelenaCore REST API.
Modules running in Docker containers use this instead of direct Python calls.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

CORE_API_BASE = os.environ.get("SELENA_CORE_API", "http://localhost:7070/api/v1")
MODULE_TOKEN = os.environ.get("MODULE_TOKEN", "")


class CoreClient:
    """HTTP client for the SelenaCore API.

    Usage:
        client = CoreClient()
        devices = await client.list_devices()
        await client.publish_event("device.state_changed", {...})
    """

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self._base = base_url or CORE_API_BASE
        self._token = token or MODULE_TOKEN

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def list_devices(self) -> list[dict[str, Any]]:
        """GET /api/v1/devices — list all registered devices."""
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self._base}/devices", headers=self._headers())
            resp.raise_for_status()
            return resp.json().get("devices", [])

    async def get_device(self, device_id: str) -> dict[str, Any] | None:
        """GET /api/v1/devices/{device_id}."""
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self._base}/devices/{device_id}", headers=self._headers()
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def create_device(
        self,
        name: str,
        type: str,
        protocol: str,
        capabilities: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/devices — register a new device."""
        import httpx
        payload = {
            "name": name,
            "type": type,
            "protocol": protocol,
            "capabilities": capabilities or [],
            "meta": meta or {},
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._base}/devices", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()

    async def update_device_state(
        self, device_id: str, state: dict[str, Any]
    ) -> dict[str, Any]:
        """PATCH /api/v1/devices/{device_id}/state."""
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.patch(
                f"{self._base}/devices/{device_id}/state",
                json={"state": state},
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def publish_event(
        self, event_type: str, source: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """POST /api/v1/events/publish."""
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._base}/events/publish",
                json={"type": event_type, "source": source, "payload": payload},
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def subscribe_events(
        self, event_types: list[str], webhook_url: str
    ) -> dict[str, Any]:
        """POST /api/v1/events/subscribe."""
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._base}/events/subscribe",
                json={"event_types": event_types, "webhook_url": webhook_url},
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def list_modules(self) -> list[dict[str, Any]]:
        """GET /api/v1/modules."""
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self._base}/modules", headers=self._headers())
            resp.raise_for_status()
            return resp.json().get("modules", [])

    async def health(self) -> dict[str, Any]:
        """GET /api/v1/health (no auth required)."""
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{self._base}/health")
            resp.raise_for_status()
            return resp.json()
