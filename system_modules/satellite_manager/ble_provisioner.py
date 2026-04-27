"""BLE discovery and Wi-Fi provisioning for ESP32 satellites.

The `bleak` import is lazy so module load doesn't require BLE stack on boxes
without Bluetooth. Scan/provision calls fail explicitly if bleak is missing.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

logger = logging.getLogger(__name__)

# Selena Provisioning Service — GATT UUIDs (fixed, firmware-side contract)
PROV_SERVICE_UUID = "a1b2c3d4-0001-1000-8000-00805f9b34fb"
CHAR_WIFI_SSID = "a1b2c3d4-0002-1000-8000-00805f9b34fb"
CHAR_WIFI_PASS = "a1b2c3d4-0003-1000-8000-00805f9b34fb"
CHAR_HUB_URL = "a1b2c3d4-0004-1000-8000-00805f9b34fb"
CHAR_DEVICE_TOKEN = "a1b2c3d4-0005-1000-8000-00805f9b34fb"
CHAR_PROV_STATUS = "a1b2c3d4-0006-1000-8000-00805f9b34fb"   # notify
CHAR_DEVICE_IP = "a1b2c3d4-0007-1000-8000-00805f9b34fb"     # notify


class BLEProvisioner:
    """Scan for and provision Selena satellite speakers via BLE."""

    async def scan(self, timeout: float = 10.0) -> list[dict]:
        try:
            from bleak import BleakScanner
        except ImportError:
            logger.warning("bleak not installed; BLE scan unavailable")
            return []

        results: list[dict] = []
        try:
            devices = await BleakScanner.discover(
                timeout=timeout, service_uuids=[PROV_SERVICE_UUID],
            )
        except Exception as exc:
            logger.error("BLE scan failed: %s", exc)
            return []

        for d in devices:
            if not d.name or not d.name.startswith("Selena-"):
                continue
            mfr_data: dict = {}
            meta = getattr(d, "metadata", None) or {}
            for raw in (meta.get("manufacturer_data") or {}).values():
                try:
                    mfr_data = json.loads(raw.decode("utf-8"))
                    break
                except Exception:
                    continue
            results.append({
                "name": d.name,
                "mac": d.address,
                "rssi": getattr(d, "rssi", 0),
                "firmware": mfr_data.get("fw", "unknown"),
                "hardware": mfr_data.get("hw", "esp32_audio_kit"),
            })
        return results

    async def provision(
        self,
        mac: str,
        wifi_ssid: str,
        wifi_pass: str,
        hub_url: str,
        device_token: str,
        on_status: Callable[[str], None] | None = None,
        ip_wait_s: float = 30.0,
    ) -> str | None:
        """Push Wi-Fi + token to ESP32 via BLE GATT. Return satellite IP or None."""
        try:
            from bleak import BleakClient
        except ImportError:
            logger.error("bleak not installed; cannot provision")
            return None

        device_ip: str | None = None
        last_status = ""

        def _on_status_notify(_handle: int, data: bytearray) -> None:
            nonlocal last_status
            last_status = data.decode("utf-8", errors="replace")
            logger.info("Satellite %s provisioning status: %s", mac, last_status)
            if on_status:
                try:
                    on_status(last_status)
                except Exception:
                    logger.exception("on_status callback failed")

        def _on_ip_notify(_handle: int, data: bytearray) -> None:
            nonlocal device_ip
            device_ip = data.decode("utf-8", errors="replace").strip()
            logger.info("Satellite %s got IP: %s", mac, device_ip)

        try:
            async with BleakClient(mac, timeout=15.0) as client:
                if not client.is_connected:
                    logger.error("Satellite %s: BLE connect failed", mac)
                    return None

                await client.start_notify(CHAR_PROV_STATUS, _on_status_notify)
                await client.start_notify(CHAR_DEVICE_IP, _on_ip_notify)

                # Order matters — token triggers the Wi-Fi connect on firmware
                await client.write_gatt_char(CHAR_WIFI_SSID, wifi_ssid.encode("utf-8"))
                await client.write_gatt_char(CHAR_WIFI_PASS, wifi_pass.encode("utf-8"))
                await client.write_gatt_char(CHAR_HUB_URL, hub_url.encode("utf-8"))
                await client.write_gatt_char(CHAR_DEVICE_TOKEN, device_token.encode("utf-8"))

                deadline = asyncio.get_event_loop().time() + ip_wait_s
                while asyncio.get_event_loop().time() < deadline:
                    if device_ip:
                        break
                    if last_status == "wifi_fail":
                        logger.error("Satellite %s: Wi-Fi connection failed", mac)
                        return None
                    await asyncio.sleep(0.5)

        except Exception:
            logger.exception("BLE provisioning failed for %s", mac)
            return None

        return device_ip
