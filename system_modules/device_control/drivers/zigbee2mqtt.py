"""
system_modules/device_control/drivers/zigbee2mqtt.py — Zigbee2MQTT adapter.

Delegates MQTT I/O to the existing protocol-bridge module via EventBus,
identical to ``MqttBridgeDriver``.  The key difference is the Z2M-specific
topic layout and the logical ↔ Z2M state translation.

Zigbee2MQTT publishes device state on::

    {base_topic}/{friendly_name}          → JSON state
    {base_topic}/{friendly_name}/set      ← JSON commands

``set_state`` publishes a ``device.command`` event that protocol-bridge
translates into the right MQTT publish.  ``stream_events`` parks forever —
real state updates arrive through protocol-bridge's external subscription.

``device.meta["zigbee2mqtt"]`` schema::

    {
        "friendly_name": str,         # Z2M friendly name (REQUIRED)
        "ieee_address":  str | None,  # e.g. "0x00158d0001a2b3c4"
        "base_topic":    str,         # default "zigbee2mqtt"
    }
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

from .base import DeviceDriver, DriverError

logger = logging.getLogger(__name__)


# ── Z2M ↔ logical state helpers ───────────────────────────────────────────


def _logical_to_z2m(state: dict[str, Any]) -> dict[str, Any]:
    """Translate SelenaCore logical keys into Zigbee2MQTT JSON payload."""
    out: dict[str, Any] = {}
    for key, value in state.items():
        if key == "on":
            out["state"] = "ON" if value else "OFF"
        elif key == "brightness":
            out["brightness"] = int(value)
        elif key == "colour_temp":
            out["color_temp"] = int(value)
        elif key == "hue":
            out.setdefault("color", {})["hue"] = int(value)
        elif key == "saturation":
            out.setdefault("color", {})["saturation"] = int(value)
        elif key == "color_xy":
            out["color"] = {"x": value[0], "y": value[1]}
        else:
            # Pass unknown keys as-is — Z2M accepts arbitrary JSON in /set
            out[key] = value
    return out


def _z2m_to_logical(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate a Zigbee2MQTT state payload into logical keys.

    Used by protocol-bridge when forwarding Z2M MQTT messages to the
    EventBus as ``device.state_changed`` events.
    """
    out: dict[str, Any] = {}
    state = payload.get("state")
    if state is not None:
        out["on"] = str(state).upper() == "ON"
    if "brightness" in payload:
        out["brightness"] = int(payload["brightness"])
    if "color_temp" in payload:
        out["colour_temp"] = int(payload["color_temp"])
    if "temperature" in payload:
        out["temperature"] = float(payload["temperature"])
    if "humidity" in payload:
        out["humidity"] = float(payload["humidity"])
    if "contact" in payload:
        out["contact"] = bool(payload["contact"])
    if "occupancy" in payload:
        out["occupancy"] = bool(payload["occupancy"])
    if "battery" in payload:
        out["battery"] = int(payload["battery"])
    color = payload.get("color")
    if isinstance(color, dict):
        if "hue" in color:
            out["hue"] = int(color["hue"])
        if "saturation" in color:
            out["saturation"] = int(color["saturation"])
    return out


# ── Driver ─────────────────────────────────────────────────────────────────


class Zigbee2MqttDriver(DeviceDriver):
    protocol = "zigbee2mqtt"

    def __init__(self, device_id: str, meta: dict[str, Any]) -> None:
        super().__init__(device_id, meta)
        cfg = (meta or {}).get("zigbee2mqtt") or {}
        self._friendly_name: str = str(cfg.get("friendly_name") or "").strip()
        self._ieee_address: str | None = cfg.get("ieee_address")
        base = str(cfg.get("base_topic") or "zigbee2mqtt").strip()
        self._topic_command: str = f"{base}/{self._friendly_name}/set"
        self._topic_state: str = f"{base}/{self._friendly_name}"

    async def connect(self) -> dict[str, Any]:
        if not self._friendly_name:
            raise DriverError(
                f"Zigbee2MqttDriver {self.device_id}: "
                "meta.zigbee2mqtt.friendly_name is missing"
            )
        # protocol-bridge already maintains the MQTT subscription.
        # Initial state is read from Device.state in the registry.
        return {}

    async def disconnect(self) -> None:
        return None

    async def set_state(self, state: dict[str, Any]) -> None:
        if self.event_publisher is None:
            raise DriverError(
                f"Zigbee2MqttDriver {self.device_id}: event_publisher not "
                "injected — device-control module must set it before set_state()"
            )
        z2m_payload = _logical_to_z2m(state)
        payload = {
            "device_id": self.device_id,
            "protocol": self.protocol,
            "command_topic": self._topic_command,
            "state": z2m_payload,
        }
        await self.event_publisher("device.command", payload)
        logger.debug(
            "Zigbee2MqttDriver: published device.command device=%s "
            "topic=%s payload=%s",
            self.device_id,
            self._topic_command,
            z2m_payload,
        )

    async def get_state(self) -> dict[str, Any]:
        # Z2M devices report state asynchronously via MQTT; there is no
        # synchronous pull through the EventBus delegation.
        return {}

    async def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        # Park forever — DeviceControlModule's external _on_state_changed
        # subscription picks up real state events from EventBus.
        while True:
            await asyncio.sleep(3600)
        # unreachable, but required to make this an async generator
        yield {}  # type: ignore[unreachable]
