"""
system_modules/import_adapters/ha_adapter.py — Home Assistant local API import adapter

Imports entities from a local Home Assistant instance via its REST API.
Converts HA entities to SelenaCore Device registry format.

HA API docs: https://developers.home-assistant.io/docs/api/rest/
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class HAEntity:
    entity_id: str
    friendly_name: str
    state: str
    domain: str       # light, switch, sensor, climate, etc.
    attributes: dict[str, Any]


class HomeAssistantAdapter:
    """Adapter for Home Assistant local REST API."""

    def __init__(self, base_url: str, token: str) -> None:
        # Ensure base_url is http/https only (SSRF safety)
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Invalid HA base URL scheme: {parsed.scheme!r}")
        self._base_url = base_url.rstrip("/")
        self._token = token

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def test_connection(self) -> bool:
        """Verify HA API is reachable. Returns True on success."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/api/", headers=self._headers)
                return resp.status_code == 200
        except Exception as exc:
            logger.warning("HA connection test failed: %s", exc)
            return False

    async def get_entities(self) -> list[HAEntity]:
        """Fetch all states from Home Assistant."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self._base_url}/api/states", headers=self._headers)
            resp.raise_for_status()
            states = resp.json()

        entities: list[HAEntity] = []
        for s in states:
            entity_id: str = s.get("entity_id", "")
            domain = entity_id.split(".")[0] if "." in entity_id else "unknown"
            attrs = s.get("attributes", {})
            entities.append(HAEntity(
                entity_id=entity_id,
                friendly_name=attrs.get("friendly_name", entity_id),
                state=s.get("state", ""),
                domain=domain,
                attributes=attrs,
            ))
        return entities

    async def call_service(self, domain: str, service: str, data: dict[str, Any]) -> bool:
        """Call a HA service (e.g., light.turn_on)."""
        url = f"{self._base_url}/api/services/{domain}/{service}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=self._headers, json=data)
            return resp.status_code in (200, 201)

    def to_selena_devices(self, entities: list[HAEntity]) -> list[dict[str, Any]]:
        """Convert HA entities to SelenaCore Device dicts for bulk registry import."""
        devices = []
        for entity in entities:
            devices.append({
                "name": entity.friendly_name,
                "device_type": entity.domain,
                "protocol": "ha_rest",
                "address": entity.entity_id,
                "state": entity.state,
                "meta": {
                    "source": "home_assistant",
                    "entity_id": entity.entity_id,
                    **{k: str(v) for k, v in entity.attributes.items()},
                },
            })
        return devices
