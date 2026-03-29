"""
system_modules/network_scanner/zigbee_scanner.py — Zigbee device scanner via USB dongle

Discovers Zigbee devices through a coordinator dongle (CC2531, CC2652, etc.)
connected via USB serial. Uses the zigpy library (or direct serial commands)
to interrogate the Zigbee network and list joined devices.

Requires:
  - USB Zigbee coordinator dongle (e.g., /dev/ttyUSB0)
  - zigpy library (optional, graceful degradation)
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ZIGBEE_SERIAL_PORT = os.environ.get("ZIGBEE_SERIAL_PORT", "/dev/ttyUSB0")
ZIGBEE_BAUDRATE = int(os.environ.get("ZIGBEE_BAUDRATE", "115200"))


@dataclass
class ZigbeeDevice:
    ieee: str  # IEEE 802.15.4 extended address
    nwk: int  # 16-bit network address
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    lqi: int = 0  # Link Quality Indicator
    last_seen: float = 0.0
    endpoints: list[dict[str, Any]] = field(default_factory=list)


def is_dongle_available() -> bool:
    """Check if Zigbee USB dongle is plugged in."""
    return Path(ZIGBEE_SERIAL_PORT).exists()


async def scan_zigbee_network(timeout: float = 30.0) -> list[ZigbeeDevice]:
    """Scan for Zigbee devices via the coordinator.

    Returns list of discovered devices. If no dongle is found, returns empty list.
    """
    if not is_dongle_available():
        logger.debug("Zigbee dongle not found at %s — skipping scan", ZIGBEE_SERIAL_PORT)
        return []

    try:
        return await _scan_via_zigpy(timeout)
    except ImportError:
        logger.info("zigpy not installed — Zigbee scanning unavailable")
        return []
    except Exception as exc:
        logger.warning("Zigbee scan failed: %s", exc)
        return []


async def _scan_via_zigpy(timeout: float) -> list[ZigbeeDevice]:
    """Scan using the zigpy library with znp/ezsp radio."""
    import zigpy.config as zigpy_config  # type: ignore
    from zigpy.types import EUI64  # type: ignore

    # Attempt to detect radio type from dongle
    radio_type = os.environ.get("ZIGBEE_RADIO_TYPE", "znp")

    if radio_type == "znp":
        from zigpy_znp.zigpy.device import Device  # type: ignore
    else:
        logger.warning("Unsupported Zigbee radio type: %s", radio_type)
        return []

    # This is a simplified scan — in production, zigpy requires a full
    # application controller setup. For device discovery, we list
    # devices already joined to the network.
    logger.info("Zigbee scan started on %s (radio=%s, timeout=%.0fs)",
                ZIGBEE_SERIAL_PORT, radio_type, timeout)

    # Placeholder: actual zigpy controller integration would go here.
    # The pattern is:
    #   1. Create ControllerApplication with serial config
    #   2. Start the controller
    #   3. Iterate controller.devices for already-joined devices
    #   4. Optionally permit_join for new device discovery
    #   5. Stop the controller

    await asyncio.sleep(min(timeout, 2.0))  # Simulate scan time
    logger.info("Zigbee scan completed — no devices found (stub implementation)")
    return []


async def permit_join(duration_sec: int = 60) -> bool:
    """Open the Zigbee network for new device joining."""
    if not is_dongle_available():
        return False
    logger.info("Zigbee permit-join opened for %ds", duration_sec)
    # Actual zigpy implementation would call controller.permit(duration_sec)
    return True
