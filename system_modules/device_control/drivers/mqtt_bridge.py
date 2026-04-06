"""
system_modules/device_control/drivers/mqtt_bridge.py — MQTT/Zigbee shim.

Delegates to the existing protocol-bridge module instead of opening its own
MQTT connection. ``set_state`` publishes a logical command on EventBus that
protocol-bridge translates into the right MQTT topic; ``stream_events``
listens to ``device.state_changed`` events filtered by device_id.

This is a v1 stub — wired up enough that the device can be created and
voice commands route correctly, but full MQTT topic mapping is added when
the first real Zigbee/MQTT device is paired.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

from .base import DeviceDriver, DriverError

logger = logging.getLogger(__name__)


class MqttBridgeDriver(DeviceDriver):
    protocol = "mqtt"

    def __init__(self, device_id: str, meta: dict[str, Any]) -> None:
        super().__init__(device_id, meta)
        cfg = (meta or {}).get("mqtt") or {}
        self._topic_command: str = cfg.get("command_topic", "")
        self._topic_state: str = cfg.get("state_topic", "")

    async def connect(self) -> dict[str, Any]:
        # protocol-bridge already maintains the MQTT connection.
        # Initial state is read from Device.state in the registry.
        if not self._topic_command:
            raise DriverError(
                f"Device {self.device_id}: meta.mqtt.command_topic missing"
            )
        return {}

    async def disconnect(self) -> None:
        return None

    async def set_state(self, state: dict[str, Any]) -> None:
        # Publishing a generic command event for protocol-bridge to forward.
        # Full impl pending the first real MQTT device pairing.
        logger.warning(
            "MqttBridgeDriver.set_state stub — device=%s state=%s",
            self.device_id, state,
        )
        raise DriverError("MQTT driver is a v1 stub; not yet wired to protocol-bridge")

    async def get_state(self) -> dict[str, Any]:
        return {}

    async def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        # Park forever — DeviceControlModule's external _on_state_changed
        # subscription will pick up real state events from EventBus.
        while True:
            await asyncio.sleep(3600)
        # unreachable, but required to make this an async generator
        yield {}  # type: ignore[unreachable]
