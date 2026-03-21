"""
system_modules/import_adapters/tuya_adapter.py — Tuya local protocol adapter

Discovers and controls Tuya smart devices on the local network using the
tinytuya library (no cloud required after initial pairing).

tinytuya docs: https://github.com/jasonacox/tinytuya
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TuyaDevice:
    device_id: str
    ip: str
    local_key: str
    name: str
    version: str = "3.3"
    dps: dict[str, Any] | None = None


class TuyaAdapter:
    """Adapter for Tuya local API via tinytuya."""

    async def scan_network(self, timeout: float = 6.0) -> list[TuyaDevice]:
        """Scan local network for Tuya devices using UDP broadcast."""
        try:
            import tinytuya  # type: ignore
        except ImportError:
            logger.warning("tinytuya not installed. Run: pip install tinytuya")
            return []

        loop = asyncio.get_event_loop()
        try:
            raw = await asyncio.wait_for(
                loop.run_in_executor(None, tinytuya.deviceScan, False, 6),
                timeout=timeout + 2,
            )
        except asyncio.TimeoutError:
            logger.warning("Tuya scan timed out")
            return []
        except Exception as exc:
            logger.error("Tuya scan error: %s", exc)
            return []

        devices = []
        for dev_id, info in raw.items():
            devices.append(TuyaDevice(
                device_id=dev_id,
                ip=info.get("ip", ""),
                local_key=info.get("key", ""),
                name=info.get("name", dev_id),
                version=str(info.get("version", "3.3")),
            ))
        return devices

    async def get_status(self, device: TuyaDevice) -> dict[str, Any] | None:
        """Fetch current DPS status from a Tuya device."""
        try:
            import tinytuya  # type: ignore
        except ImportError:
            return None

        loop = asyncio.get_event_loop()
        try:
            d = tinytuya.OutletDevice(
                dev_id=device.device_id,
                address=device.ip,
                local_key=device.local_key,
                version=float(device.version),
            )
            status = await loop.run_in_executor(None, d.status)
            return status.get("dps") if status else None
        except Exception as exc:
            logger.warning("Tuya status error for %s: %s", device.ip, exc)
            return None

    async def set_dps(self, device: TuyaDevice, dps: dict[str, Any]) -> bool:
        """Set DPS values on a Tuya device."""
        try:
            import tinytuya  # type: ignore
        except ImportError:
            return False

        loop = asyncio.get_event_loop()
        try:
            d = tinytuya.OutletDevice(
                dev_id=device.device_id,
                address=device.ip,
                local_key=device.local_key,
                version=float(device.version),
            )
            await loop.run_in_executor(None, lambda: d.set_multiple_values(dps))
            return True
        except Exception as exc:
            logger.warning("Tuya set error for %s: %s", device.ip, exc)
            return False

    def to_selena_devices(self, devices: list[TuyaDevice]) -> list[dict[str, Any]]:
        return [
            {
                "name": dev.name,
                "device_type": "tuya_device",
                "protocol": "tuya_local",
                "address": dev.ip,
                "state": "unknown",
                "meta": {
                    "source": "tuya",
                    "device_id": dev.device_id,
                    "version": dev.version,
                },
            }
            for dev in devices
        ]
