"""
system_modules/device_control/drivers/philips_hue.py — Hue REST API driver.

Controls Philips Hue lights (and Hue-compatible emulators) through the
standard Hue Bridge REST API using ``httpx``.  No external pip package
required — ``httpx`` ships with the container.

Works with:
  - Real Philips Hue Bridges (register via button-press → obtain token)
  - Hue-compatible emulators / SDS bridges (pre-set token, custom port)

The Hue Bridge doesn't push state updates, so ``stream_events`` polls every
3 seconds and yields only when the state actually changes (same pattern as
``gree.py``).

``device.meta["philips_hue"]`` schema::

    {
        "api_host":    str,         # Base URL, e.g. "http://192.168.1.100"
                                    #   or "http://192.168.1.254:7000"
        "token":       str,         # API token / username (REQUIRED)
        "light_id":    int | str,   # light id on the bridge (REQUIRED)
    }

Hue REST API endpoints used::

    GET  /api/<token>/lights/<id>        → light object with "state" sub-dict
    PUT  /api/<token>/lights/<id>/state  → apply partial state update

Logical state shape::

    {
        "on":          bool,
        "brightness":  int,         # 0-254
        "colour_temp": int | None,  # mireds (153-500)
        "hue":         int | None,  # 0-65535
        "saturation":  int | None,  # 0-254
        "reachable":   bool,        # read-only
    }
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

import httpx

from .base import DeviceDriver, DriverError

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 3.0
HTTP_TIMEOUT = 10.0


# ── State translation helpers ─────────────────────────────────────────────


def _to_logical(light_data: dict[str, Any]) -> dict[str, Any]:
    """Translate Hue REST light object into SelenaCore logical keys."""
    state = light_data.get("state") or {}
    out: dict[str, Any] = {}
    if "on" in state:
        out["on"] = bool(state["on"])
    if "bri" in state:
        out["brightness"] = int(state["bri"])
    if "ct" in state:
        out["colour_temp"] = int(state["ct"])
    if "hue" in state:
        out["hue"] = int(state["hue"])
    if "sat" in state:
        out["saturation"] = int(state["sat"])
    if "reachable" in state:
        out["reachable"] = bool(state["reachable"])
    return out


def _logical_to_hue(state: dict[str, Any]) -> dict[str, Any]:
    """Translate logical keys into Hue REST state body."""
    out: dict[str, Any] = {}
    for key, value in state.items():
        if key == "on":
            out["on"] = bool(value)
        elif key == "brightness":
            out["bri"] = int(value)
        elif key == "colour_temp":
            out["ct"] = int(value)
        elif key == "hue":
            out["hue"] = int(value)
        elif key == "saturation":
            out["sat"] = int(value)
        # "reachable" is read-only — silently skip
    return out


# ── Driver ─────────────────────────────────────────────────────────────────


class PhilipsHueDriver(DeviceDriver):
    protocol = "philips_hue"

    def __init__(self, device_id: str, meta: dict[str, Any]) -> None:
        super().__init__(device_id, meta)
        cfg = (meta or {}).get("philips_hue") or {}
        api_host = str(cfg.get("api_host") or "").strip().rstrip("/")
        self._token: str = str(cfg.get("token") or "").strip()
        self._light_id: str = str(cfg.get("light_id") or "").strip()
        # Build base URL: http(s)://<host>/api/<token>
        self._base_url: str = f"{api_host}/api/{self._token}" if api_host else ""
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._last_state: dict[str, Any] | None = None

    async def connect(self) -> dict[str, Any]:
        if not self._base_url:
            raise DriverError(
                f"PhilipsHueDriver {self.device_id}: "
                "meta.philips_hue.api_host is missing"
            )
        if not self._token:
            raise DriverError(
                f"PhilipsHueDriver {self.device_id}: "
                "meta.philips_hue.token is missing"
            )
        if not self._light_id:
            raise DriverError(
                f"PhilipsHueDriver {self.device_id}: "
                "meta.philips_hue.light_id is missing"
            )
        async with self._lock:
            if self._client is not None:
                await self._client.aclose()
            self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
            url = f"{self._base_url}/lights/{self._light_id}"
            try:
                resp = await self._client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as exc:
                raise DriverError(
                    f"Hue API error: {exc.response.status_code} on GET {url}"
                ) from exc
            except Exception as exc:
                raise DriverError(
                    f"Hue connect failed ({url}): {exc}"
                ) from exc
        state = _to_logical(data)
        self._last_state = dict(state)
        return state

    async def disconnect(self) -> None:
        async with self._lock:
            client = self._client
            self._client = None
        if client is not None:
            await client.aclose()

    async def set_state(self, state: dict[str, Any]) -> None:
        if not state:
            return
        if self._client is None:
            await self.connect()
        hue_cmd = _logical_to_hue(state)
        if not hue_cmd:
            return
        url = f"{self._base_url}/lights/{self._light_id}/state"
        async with self._lock:
            try:
                resp = await self._client.put(url, json=hue_cmd)  # type: ignore[union-attr]
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise DriverError(
                    f"Hue set_state error: {exc.response.status_code} on "
                    f"PUT {url} body={hue_cmd}"
                ) from exc
            except Exception as exc:
                raise DriverError(
                    f"Hue set_state failed ({url}): {exc}"
                ) from exc

    async def get_state(self) -> dict[str, Any]:
        if self._client is None:
            return await self.connect()
        url = f"{self._base_url}/lights/{self._light_id}"
        async with self._lock:
            try:
                resp = await self._client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as exc:
                raise DriverError(
                    f"Hue get_state error: {exc.response.status_code}"
                ) from exc
            except Exception as exc:
                raise DriverError(
                    f"Hue get_state failed ({url}): {exc}"
                ) from exc
            state = _to_logical(data)
        self._last_state = dict(state)
        return state

    async def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        if self._client is None:
            await self.connect()
        url = f"{self._base_url}/lights/{self._light_id}"
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            async with self._lock:
                try:
                    resp = await self._client.get(url)  # type: ignore[union-attr]
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    raise DriverError(
                        f"Hue poll failed for {self.device_id}: {exc}"
                    ) from exc
                state = _to_logical(data)
            if state != self._last_state:
                self._last_state = dict(state)
                yield state
