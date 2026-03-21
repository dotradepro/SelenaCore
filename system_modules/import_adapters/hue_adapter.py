"""
system_modules/import_adapters/hue_adapter.py — Philips Hue local API adapter

Discovers Hue Bridge on the local network and retrieves lights/groups/scenes
via the Hue CLIP API v2 (HTTPS, no cloud needed after pairing).

Discovery: mDNS (_hue._tcp) or UPnP or direct IP.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

HUE_DISCOVERY_URL = "https://discovery.meethue.com/"


@dataclass
class HueBridge:
    bridge_id: str
    ip: str
    username: str = ""  # Application key (created during pairing)


@dataclass
class HueLight:
    light_id: str
    name: str
    on: bool
    brightness: int    # 0-254
    color_xy: tuple[float, float] | None
    reachable: bool
    raw: dict[str, Any]


class HueAdapter:
    """Adapter for Philips Hue Bridge local CLIP API v2."""

    def __init__(self, bridge: HueBridge) -> None:
        self._bridge = bridge
        # Hue v2 base URL
        self._base = f"https://{bridge.ip}/clip/v2"
        # Accept self-signed cert from bridge
        self._verify = False

    @property
    def _headers(self) -> dict[str, str]:
        return {"hue-application-key": self._bridge.username}

    async def get_lights(self) -> list[HueLight]:
        """Fetch all lights from the bridge."""
        async with httpx.AsyncClient(verify=self._verify, timeout=10) as client:
            resp = await client.get(f"{self._base}/resource/light", headers=self._headers)
            resp.raise_for_status()
            data = resp.json()

        lights: list[HueLight] = []
        for item in data.get("data", []):
            state = item.get("on", {})
            dimming = item.get("dimming", {})
            color = item.get("color", {})
            xy = None
            if "xy" in color:
                xy = (color["xy"].get("x", 0), color["xy"].get("y", 0))
            lights.append(HueLight(
                light_id=item["id"],
                name=item.get("metadata", {}).get("name", item["id"]),
                on=state.get("on", False),
                brightness=int(dimming.get("brightness", 0)),
                color_xy=xy,
                reachable=item.get("status", {}).get("connectivity", {}).get("status") == "connected",
                raw=item,
            ))
        return lights

    async def set_light_state(
        self, light_id: str, on: bool | None = None, brightness: int | None = None
    ) -> bool:
        """Set a light's on/off state and brightness."""
        payload: dict[str, Any] = {}
        if on is not None:
            payload["on"] = {"on": on}
        if brightness is not None:
            payload["dimming"] = {"brightness": max(0, min(100, brightness))}

        if not payload:
            return True

        url = f"{self._base}/resource/light/{light_id}"
        async with httpx.AsyncClient(verify=self._verify, timeout=10) as client:
            resp = await client.put(url, headers=self._headers, json=payload)
            return resp.status_code in (200, 207)

    @staticmethod
    async def discover_bridges() -> list[dict[str, str]]:
        """Discover Hue bridges using Philips cloud discovery endpoint."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(HUE_DISCOVERY_URL)
                resp.raise_for_status()
                return resp.json()  # [{"id": "...", "internalipaddress": "..."}]
        except Exception as exc:
            logger.warning("Hue bridge discovery failed: %s", exc)
            return []

    def to_selena_devices(self, lights: list[HueLight]) -> list[dict[str, Any]]:
        return [
            {
                "name": light.name,
                "device_type": "smart_light",
                "protocol": "hue_clip_v2",
                "address": self._bridge.ip,
                "state": "on" if light.on else "off",
                "meta": {
                    "source": "philips_hue",
                    "light_id": light.light_id,
                    "bridge_id": self._bridge.bridge_id,
                    "brightness": light.brightness,
                    "reachable": str(light.reachable),
                },
            }
            for light in lights
        ]
