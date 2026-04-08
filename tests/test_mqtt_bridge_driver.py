"""MqttBridgeDriver — verify EventBus delegation contract.

The driver must publish a ``device.command`` event whenever ``set_state``
is called; ``protocol_bridge`` is the actual MQTT publisher.
"""
from __future__ import annotations

import asyncio

import pytest

from system_modules.device_control.drivers.base import DriverError
from system_modules.device_control.drivers.mqtt_bridge import MqttBridgeDriver


@pytest.mark.asyncio
async def test_set_state_publishes_device_command_event():
    captured: list[tuple[str, dict]] = []

    async def fake_publisher(event_type: str, payload: dict) -> None:
        captured.append((event_type, payload))

    drv = MqttBridgeDriver(
        device_id="dev-1",
        meta={"mqtt": {"command_topic": "home/lamp/set",
                       "state_topic": "home/lamp"}},
    )
    drv.event_publisher = fake_publisher

    await drv.set_state({"on": True})

    assert len(captured) == 1
    event_type, payload = captured[0]
    assert event_type == "device.command"
    assert payload["device_id"] == "dev-1"
    assert payload["protocol"] == "mqtt"
    assert payload["command_topic"] == "home/lamp/set"
    assert payload["state"] == {"on": True}


@pytest.mark.asyncio
async def test_set_state_without_publisher_raises():
    drv = MqttBridgeDriver(
        device_id="dev-1",
        meta={"mqtt": {"command_topic": "home/lamp/set"}},
    )
    # event_publisher is None — DeviceControlModule did not inject it.
    with pytest.raises(DriverError, match="event_publisher not injected"):
        await drv.set_state({"on": True})


@pytest.mark.asyncio
async def test_connect_requires_command_topic():
    drv = MqttBridgeDriver(device_id="dev-1", meta={"mqtt": {}})
    with pytest.raises(DriverError, match="command_topic missing"):
        await drv.connect()
