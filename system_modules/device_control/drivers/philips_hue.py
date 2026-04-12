"""
system_modules/device_control/drivers/philips_hue.py — Philips Hue driver.

Controls Philips Hue lights through the Hue Bridge LAN REST API using the
``phue`` library.  Local-only, no cloud account required.

The Hue Bridge doesn't push state updates, so ``stream_events`` polls every
3 seconds and yields only when the state actually changes (same pattern as
``gree.py``).  The ``phue`` library is synchronous — all bridge calls are
wrapped with ``asyncio.to_thread``.

First-time pairing requires a physical button press on the Hue Bridge.
``connect()`` detects the ``PhueRegistrationException`` and wraps it in a
clear ``DriverError`` message for the UI.  After successful pairing, the
bridge-generated username is persisted into ``device.meta["philips_hue"]``
so subsequent reconnects are automatic.

``device.meta["philips_hue"]`` schema::

    {
        "bridge_ip":   str,         # Hue Bridge LAN IP (REQUIRED)
        "light_id":    int | str,   # light id on the bridge (REQUIRED)
        "username":    str | None,  # API username (populated after pairing)
        "light_name":  str | None,  # display name from bridge (diagnostic)
    }

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

from .base import DeviceDriver, DriverError

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 3.0


# ── State translation helpers ─────────────────────────────────────────────


def _to_logical(light_data: dict[str, Any]) -> dict[str, Any]:
    """Translate phue light state dict into SelenaCore logical keys."""
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
    """Translate logical keys into phue ``set_light`` kwargs."""
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


# ── Driver ───────────────��─────────────────────────────────────────────────


class PhilipsHueDriver(DeviceDriver):
    protocol = "philips_hue"

    def __init__(self, device_id: str, meta: dict[str, Any]) -> None:
        super().__init__(device_id, meta)
        cfg = (meta or {}).get("philips_hue") or {}
        self._bridge_ip: str = str(cfg.get("bridge_ip") or "").strip()
        self._light_id: int | str = cfg.get("light_id", "")
        self._username: str | None = cfg.get("username") or None
        self._bridge: Any = None
        self._lock = asyncio.Lock()
        self._last_state: dict[str, Any] | None = None

    def _build_bridge(self) -> Any:
        """Create a ``phue.Bridge`` instance (lazy import)."""
        try:
            from phue import Bridge  # type: ignore
        except ImportError as exc:
            raise DriverError(
                "phue not installed — open device-control settings → "
                "Providers → Philips Hue and click Install"
            ) from exc
        if not self._bridge_ip:
            raise DriverError(
                f"PhilipsHueDriver {self.device_id}: "
                "meta.philips_hue.bridge_ip is missing"
            )
        # phue stores its config in ~/.python_hue by default.
        # Passing ``username`` skips the registration step if we already
        # have one from a previous pairing.
        bridge = Bridge(ip=self._bridge_ip, username=self._username)
        try:
            bridge.connect()
        except Exception as exc:
            exc_name = type(exc).__name__
            if "Registration" in exc_name or "PhueRegistration" in exc_name:
                raise DriverError(
                    f"Hue Bridge at {self._bridge_ip}: press the physical "
                    "button on the bridge, then retry"
                ) from exc
            raise DriverError(
                f"Hue Bridge connect failed ({self._bridge_ip}): {exc}"
            ) from exc
        return bridge

    async def connect(self) -> dict[str, Any]:
        if not self._bridge_ip:
            raise DriverError(
                f"PhilipsHueDriver {self.device_id}: "
                "meta.philips_hue.bridge_ip is missing"
            )
        if not self._light_id and self._light_id != 0:
            raise DriverError(
                f"PhilipsHueDriver {self.device_id}: "
                "meta.philips_hue.light_id is missing"
            )
        async with self._lock:
            try:
                self._bridge = await asyncio.to_thread(self._build_bridge)
            except DriverError:
                raise
            except Exception as exc:
                raise DriverError(
                    f"Hue Bridge connect failed ({self._bridge_ip}): {exc}"
                ) from exc
            # Persist the username so future reconnects skip button-press.
            new_username = getattr(self._bridge, "username", None)
            if new_username and new_username != self._username:
                self._username = new_username
                hue_meta = self.meta.setdefault("philips_hue", {})
                hue_meta["username"] = new_username
            # Fetch initial state.
            try:
                light_id = int(self._light_id)
            except (TypeError, ValueError):
                light_id = self._light_id
            try:
                data = await asyncio.to_thread(
                    self._bridge.get_light, light_id,
                )
            except Exception as exc:
                raise DriverError(
                    f"Hue get_light({self._light_id}) failed: {exc}"
                ) from exc
        state = _to_logical(data)
        self._last_state = dict(state)
        return state

    async def disconnect(self) -> None:
        async with self._lock:
            self._bridge = None

    async def set_state(self, state: dict[str, Any]) -> None:
        if not state:
            return
        if self._bridge is None:
            await self.connect()
        hue_cmd = _logical_to_hue(state)
        if not hue_cmd:
            return
        try:
            light_id = int(self._light_id)
        except (TypeError, ValueError):
            light_id = self._light_id
        async with self._lock:
            try:
                await asyncio.to_thread(
                    self._bridge.set_light, light_id, hue_cmd,
                )
            except Exception as exc:
                raise DriverError(
                    f"Hue set_light({self._light_id}) failed: {exc}"
                ) from exc

    async def get_state(self) -> dict[str, Any]:
        if self._bridge is None:
            return await self.connect()
        try:
            light_id = int(self._light_id)
        except (TypeError, ValueError):
            light_id = self._light_id
        async with self._lock:
            try:
                data = await asyncio.to_thread(
                    self._bridge.get_light, light_id,
                )
            except Exception as exc:
                raise DriverError(
                    f"Hue get_light({self._light_id}) failed: {exc}"
                ) from exc
            state = _to_logical(data)
        self._last_state = dict(state)
        return state

    async def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        if self._bridge is None:
            await self.connect()
        try:
            light_id = int(self._light_id)
        except (TypeError, ValueError):
            light_id = self._light_id
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            async with self._lock:
                try:
                    data = await asyncio.to_thread(
                        self._bridge.get_light, light_id,
                    )
                except Exception as exc:
                    raise DriverError(
                        f"Hue poll failed for {self.device_id}: {exc}"
                    ) from exc
                state = _to_logical(data)
            if state != self._last_state:
                self._last_state = dict(state)
                yield state
