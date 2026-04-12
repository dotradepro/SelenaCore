"""
system_modules/device_control/drivers/esphome.py — ESPHome native API driver.

Connects to ESPHome devices over the LAN using the ``aioesphomeapi`` library.
Each device runs its own native API server on port 6053 (configurable), so
every ``ESPHomeDriver`` instance holds its own connection — no shared singleton
like Matter.

Push-based: ``subscribe_states()`` invokes a callback on every state change;
the callback pushes into an ``asyncio.Queue`` and ``stream_events()`` yields
from it (same pattern as ``matter.py``).

``device.meta["esphome"]`` schema::

    {
        "ip":              str,         # LAN IP (REQUIRED)
        "port":            int,         # default 6053
        "password":        str | None,  # legacy API password (pre-2023)
        "encryption_key":  str | None,  # noise encryption key (modern)
        "device_name":     str | None,  # ESPHome device name (diagnostic)
    }

Logical state shape varies by entity type:

    # Light
    {"on": bool, "brightness": int, "colour_temp": int}
    # Switch / outlet
    {"on": bool}
    # Sensor
    {"temperature": float, "humidity": float, "battery": int, ...}
    # Binary sensor
    {"contact": bool}  or  {"occupancy": bool}
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

from .base import DeviceDriver, DriverError

logger = logging.getLogger(__name__)

DEFAULT_PORT = 6053


# ── Entity type classification ─────────────────────────────────────────────

# ESPHome sensor device_class values that map to specific logical keys.
_SENSOR_KEY_MAP: dict[str, str] = {
    "temperature": "temperature",
    "humidity": "humidity",
    "battery": "battery",
    "power": "watts",
    "voltage": "volts",
    "current": "amps",
    "energy": "energy_kwh",
    "pressure": "pressure",
    "illuminance": "illuminance",
}

_BINARY_SENSOR_KEY_MAP: dict[str, str] = {
    "door": "contact",
    "window": "contact",
    "garage_door": "contact",
    "opening": "contact",
    "motion": "occupancy",
    "occupancy": "occupancy",
    "presence": "occupancy",
    "moisture": "moisture",
    "smoke": "smoke",
    "gas": "gas",
}


# ── Driver ─────────────────────────────────────────────────────────────────


class ESPHomeDriver(DeviceDriver):
    protocol = "esphome"

    def __init__(self, device_id: str, meta: dict[str, Any]) -> None:
        super().__init__(device_id, meta)
        cfg = (meta or {}).get("esphome") or {}
        self._ip: str = str(cfg.get("ip") or "").strip()
        self._port: int = int(cfg.get("port") or DEFAULT_PORT)
        self._password: str = str(cfg.get("password") or "")
        self._encryption_key: str | None = cfg.get("encryption_key") or None
        self._client: Any = None
        self._queue: asyncio.Queue[dict[str, Any] | DriverError] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._last_state: dict[str, Any] = {}
        # Populated during connect() — maps aioesphomeapi entity key (int)
        # to (logical_key, entity_info_type_name).
        self._entity_map: dict[int, tuple[str, str]] = {}
        # Tracks light entity keys for set_state dispatch.
        self._light_keys: set[int] = set()
        self._switch_keys: set[int] = set()
        # Cached metering snapshot.
        self._last_metering: dict[str, float] | None = None

    def _build_client(self) -> Any:
        """Create an ``aioesphomeapi.APIClient`` (lazy import)."""
        try:
            from aioesphomeapi import APIClient  # type: ignore
        except ImportError as exc:
            raise DriverError(
                "aioesphomeapi not installed — open device-control settings → "
                "Providers → ESPHome and click Install"
            ) from exc
        if not self._ip:
            raise DriverError(
                f"ESPHomeDriver {self.device_id}: meta.esphome.ip is missing"
            )
        return APIClient(
            address=self._ip,
            port=self._port,
            password=self._password,
            noise_psk=self._encryption_key,
        )

    def _on_state_change(self, state: Any) -> None:
        """Callback invoked by aioesphomeapi on every state push."""
        delta = self._translate_state(state)
        if delta:
            self._last_state.update(delta)
            try:
                self._queue.put_nowait(dict(self._last_state))
            except asyncio.QueueFull:
                pass

    def _on_disconnect(self) -> None:
        """Called when the native API connection drops."""
        try:
            self._queue.put_nowait(
                DriverError("ESPHome device disconnected")
            )
        except asyncio.QueueFull:
            pass

    def _translate_state(self, state_obj: Any) -> dict[str, Any]:
        """Convert an aioesphomeapi state object into logical keys."""
        out: dict[str, Any] = {}
        key = getattr(state_obj, "key", None)
        if key is None:
            return out
        type_name = type(state_obj).__name__

        if type_name == "LightState":
            out["on"] = bool(getattr(state_obj, "state", False))
            brightness = getattr(state_obj, "brightness", None)
            if brightness is not None:
                # aioesphomeapi brightness is 0.0-1.0 → we use 0-254
                out["brightness"] = int(round(float(brightness) * 254))
            ct = getattr(state_obj, "color_temperature", None)
            if ct is not None and float(ct) > 0:
                out["colour_temp"] = int(round(float(ct)))

        elif type_name == "SwitchState":
            out["on"] = bool(getattr(state_obj, "state", False))

        elif type_name == "BinarySensorState":
            logical_key, _etype = self._entity_map.get(key, ("on", ""))
            out[logical_key] = bool(getattr(state_obj, "state", False))

        elif type_name == "SensorState":
            logical_key, _etype = self._entity_map.get(key, ("value", ""))
            value = getattr(state_obj, "state", None)
            if value is not None and not getattr(state_obj, "missing_state", False):
                out[logical_key] = float(value)
                # Cache metering if it's a power/voltage/current sensor.
                if logical_key == "watts":
                    m = self._last_metering or {}
                    m["watts"] = float(value)
                    self._last_metering = m
                elif logical_key == "volts":
                    m = self._last_metering or {}
                    m["volts"] = float(value)
                    self._last_metering = m
                elif logical_key == "amps":
                    m = self._last_metering or {}
                    m["amps"] = float(value)
                    self._last_metering = m

        elif type_name == "FanState":
            out["on"] = bool(getattr(state_obj, "state", False))
            speed = getattr(state_obj, "speed_level", None)
            if speed is not None:
                out["fan_speed"] = int(speed)

        return out

    async def connect(self) -> dict[str, Any]:
        async with self._lock:
            if self._client is not None:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
            self._client = self._build_client()
            try:
                await self._client.connect(on_stop=self._on_disconnect, login=True)
            except DriverError:
                raise
            except Exception as exc:
                self._client = None
                raise DriverError(
                    f"ESPHome connect failed ({self._ip}:{self._port}): {exc}"
                ) from exc

            # Discover entities.
            try:
                entities, _services = await self._client.list_entities_services()
            except Exception as exc:
                raise DriverError(
                    f"ESPHome list_entities failed ({self._ip}): {exc}"
                ) from exc

            self._entity_map.clear()
            self._light_keys.clear()
            self._switch_keys.clear()

            for entity in entities:
                etype = type(entity).__name__
                ekey = getattr(entity, "key", None)
                if ekey is None:
                    continue

                if etype == "LightInfo":
                    self._light_keys.add(ekey)
                    self._entity_map[ekey] = ("on", "light")
                elif etype == "SwitchInfo":
                    self._switch_keys.add(ekey)
                    self._entity_map[ekey] = ("on", "switch")
                elif etype == "SensorInfo":
                    device_class = str(
                        getattr(entity, "device_class", "") or ""
                    ).lower()
                    logical = _SENSOR_KEY_MAP.get(device_class, device_class or "value")
                    self._entity_map[ekey] = (logical, "sensor")
                elif etype == "BinarySensorInfo":
                    device_class = str(
                        getattr(entity, "device_class", "") or ""
                    ).lower()
                    logical = _BINARY_SENSOR_KEY_MAP.get(device_class, "on")
                    self._entity_map[ekey] = (logical, "binary_sensor")
                elif etype == "FanInfo":
                    self._entity_map[ekey] = ("on", "fan")

            # Subscribe to state updates.
            try:
                await self._client.subscribe_states(self._on_state_change)
            except Exception as exc:
                raise DriverError(
                    f"ESPHome subscribe_states failed ({self._ip}): {exc}"
                ) from exc

        return dict(self._last_state)

    async def disconnect(self) -> None:
        async with self._lock:
            client = self._client
            self._client = None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def set_state(self, state: dict[str, Any]) -> None:
        if not state or self._client is None:
            return
        async with self._lock:
            try:
                # Dispatch to the first matching entity type.
                if self._light_keys:
                    light_key = next(iter(self._light_keys))
                    cmd: dict[str, Any] = {}
                    if "on" in state:
                        cmd["state"] = bool(state["on"])
                    if "brightness" in state:
                        # Convert 0-254 → 0.0-1.0
                        cmd["brightness"] = float(state["brightness"]) / 254.0
                    if "colour_temp" in state:
                        cmd["color_temperature"] = float(state["colour_temp"])
                    await self._client.light_command(key=light_key, **cmd)

                elif self._switch_keys:
                    switch_key = next(iter(self._switch_keys))
                    if "on" in state:
                        await self._client.switch_command(
                            key=switch_key, state=bool(state["on"]),
                        )
            except Exception as exc:
                raise DriverError(
                    f"ESPHome set_state failed ({self._ip}): {exc}"
                ) from exc

    async def get_state(self) -> dict[str, Any]:
        return dict(self._last_state)

    async def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        if self._client is None:
            await self.connect()
        while True:
            item = await self._queue.get()
            if isinstance(item, DriverError):
                raise item
            yield item

    def consume_metering(self) -> dict[str, float] | None:
        m = self._last_metering
        self._last_metering = None
        return m
