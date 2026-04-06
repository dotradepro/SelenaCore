"""
system_modules/device_control/drivers/registry.py — Driver lookup.

Maps ``Device.protocol`` (string) to a concrete driver class. To add a new
driver: drop a file in this folder and register it here.
"""
from __future__ import annotations

from typing import Any

from .base import DeviceDriver, DriverError
from .mqtt_bridge import MqttBridgeDriver
from .tuya_cloud import TuyaCloudDriver
from .tuya_local import TuyaLocalDriver

DRIVERS: dict[str, type[DeviceDriver]] = {
    "tuya_local": TuyaLocalDriver,
    "tuya_cloud": TuyaCloudDriver,
    "mqtt": MqttBridgeDriver,
}


def get_driver(device_id: str, protocol: str, meta: dict[str, Any]) -> DeviceDriver:
    """Instantiate the right driver for ``device.protocol``.

    Raises DriverError if the protocol is unknown so the watcher loop can
    log and skip that device without crashing the module.
    """
    cls = DRIVERS.get(protocol)
    if cls is None:
        raise DriverError(f"Unknown driver protocol: {protocol!r}")
    return cls(device_id, meta)


def list_driver_types() -> list[dict[str, Any]]:
    """Return metadata for the UI dropdown in settings.html → Add device."""
    return [
        {
            "id": "tuya_local",
            "name": "Tuya (local LAN)",
            "needs_cloud": False,
            "fields": [
                "tuya.device_id",
                "tuya.local_key",
                "tuya.ip",
                "tuya.version",
                "tuya.dps_map",
            ],
        },
        {
            "id": "tuya_cloud",
            "name": "Tuya (cloud)",
            "needs_cloud": True,
            "fields": [
                "tuya.cloud_device_id",
                "tuya.code_map",
            ],
        },
        {
            "id": "mqtt",
            "name": "MQTT / Zigbee (via protocol-bridge)",
            "needs_cloud": False,
            "stub": True,
            "fields": [
                "mqtt.command_topic",
                "mqtt.state_topic",
            ],
        },
    ]
