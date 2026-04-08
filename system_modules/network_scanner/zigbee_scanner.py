"""
system_modules/network_scanner/zigbee_scanner.py — Zigbee device scanner via USB dongle

Discovers Zigbee devices through a coordinator dongle (CC2531, CC2652, etc.)
connected via USB serial. Uses the zigpy library to spin up a temporary
ControllerApplication, list already-joined devices, and tear it down.

Requires:
  - USB Zigbee coordinator dongle (e.g., /dev/ttyUSB0)
  - zigpy + a radio driver (zigpy-znp by default; install via the optional
    ``[zigbee]`` extra in requirements.txt). Graceful degradation when
    libraries are missing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ZIGBEE_SERIAL_PORT = os.environ.get("ZIGBEE_SERIAL_PORT", "/dev/ttyUSB0")
ZIGBEE_BAUDRATE = int(os.environ.get("ZIGBEE_BAUDRATE", "115200"))
ZIGBEE_RADIO_TYPE = os.environ.get("ZIGBEE_RADIO_TYPE", "znp")

#: Backend selector — only one process may own /dev/ttyUSB0 at a time.
#:
#:   "zigpy"  — this scanner spins up an in-process zigpy ControllerApplication
#:              and talks to the dongle directly. Use this when zigbee2mqtt is
#:              NOT running on the host.
#:   "z2m"    — assume zigbee2mqtt (or another external service) owns the
#:              dongle. The scanner becomes a no-op so we don't fight z2m
#:              for serial port ownership. Device discovery still works via
#:              the protocol_bridge module which subscribes to z2m's MQTT
#:              topics — see system_modules/protocol_bridge/bridge.py.
#:   "none"   — disable Zigbee scanning entirely.
#:
#: Override via env: ``ZIGBEE_BACKEND=z2m``.
_VALID_BACKENDS = ("zigpy", "z2m", "none")


def _resolve_backend() -> str:
    raw = os.environ.get("ZIGBEE_BACKEND", "zigpy")
    backend = (raw or "").strip().lower()
    if backend not in _VALID_BACKENDS:
        # Don't silently coerce typos like "zigbee2mqtt" or "ZIGPY-znp"
        # into "none" — make the user aware that their config is wrong.
        logger.warning(
            "zigbee_scanner: unknown ZIGBEE_BACKEND value %r — falling back "
            "to 'none' (valid: %s)",
            raw, ", ".join(_VALID_BACKENDS),
        )
        return "none"
    return backend


ZIGBEE_BACKEND = _resolve_backend()

#: Singleton controller — created on first scan/permit_join, kept alive so
#: subsequent calls don't pay the bind/start cost. Cleared on shutdown.
_controller: Any = None
_controller_lock = asyncio.Lock()


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
    """Check if we can drive the Zigbee USB dongle directly.

    Returns ``False`` if the dongle file doesn't exist OR if a competing
    backend (zigbee2mqtt) is configured to own it. We must never open
    the same serial port as another process — doing so corrupts the
    Zigbee network state and can brick the coordinator.
    """
    if ZIGBEE_BACKEND != "zigpy":
        return False
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


async def _get_controller() -> Any:
    """Lazily create and start a zigpy ControllerApplication.

    Cached process-wide. Raises ImportError if the optional ``[zigbee]``
    extra (``zigpy`` + a radio driver) is not installed — caller catches.
    """
    global _controller
    async with _controller_lock:
        if _controller is not None:
            return _controller

        import zigpy.config as zigpy_config  # type: ignore

        if ZIGBEE_RADIO_TYPE == "znp":
            from zigpy_znp.zigbee.application import (  # type: ignore
                ControllerApplication,
            )
        elif ZIGBEE_RADIO_TYPE == "ezsp":
            from bellows.zigbee.application import (  # type: ignore
                ControllerApplication,
            )
        else:
            raise RuntimeError(f"Unsupported ZIGBEE_RADIO_TYPE: {ZIGBEE_RADIO_TYPE}")

        config = ControllerApplication.SCHEMA({
            zigpy_config.CONF_DEVICE: {
                zigpy_config.CONF_DEVICE_PATH: ZIGBEE_SERIAL_PORT,
                zigpy_config.CONF_DEVICE_BAUDRATE: ZIGBEE_BAUDRATE,
            },
        })
        app = await ControllerApplication.new(
            config=config,
            auto_form=True,
            start_radio=True,
        )
        _controller = app
        logger.info(
            "Zigbee controller started on %s (radio=%s)",
            ZIGBEE_SERIAL_PORT, ZIGBEE_RADIO_TYPE,
        )
        return _controller


async def _scan_via_zigpy(timeout: float) -> list[ZigbeeDevice]:
    """Scan using the zigpy library with znp/ezsp radio.

    Lists devices already joined to the coordinator. Pair new devices via
    ``permit_join`` first; this function does not initiate joining itself.
    """
    app = await _get_controller()

    out: list[ZigbeeDevice] = []
    now = time.time()
    for ieee, device in app.devices.items():
        try:
            endpoints: list[dict[str, Any]] = []
            for ep_id, ep in device.endpoints.items():
                if ep_id == 0:  # ZDO
                    continue
                endpoints.append({
                    "endpoint_id": ep_id,
                    "profile_id": getattr(ep, "profile_id", None),
                    "device_type": getattr(ep, "device_type", None),
                    "in_clusters": list(getattr(ep, "in_clusters", {}).keys()),
                    "out_clusters": list(getattr(ep, "out_clusters", {}).keys()),
                })
            out.append(ZigbeeDevice(
                ieee=str(ieee),
                nwk=int(getattr(device, "nwk", 0)),
                name=getattr(device, "name", "") or "",
                manufacturer=getattr(device, "manufacturer", "") or "",
                model=getattr(device, "model", "") or "",
                lqi=int(getattr(device, "lqi", 0) or 0),
                last_seen=float(getattr(device, "last_seen", now) or now),
                endpoints=endpoints,
            ))
        except Exception as exc:
            logger.debug("Skipping malformed Zigbee device %s: %s", ieee, exc)

    logger.info(
        "Zigbee scan completed: %d device(s) joined to coordinator", len(out),
    )
    return out


async def permit_join(duration_sec: int = 60) -> bool:
    """Open the Zigbee network for new device joining."""
    if not is_dongle_available():
        return False
    try:
        app = await _get_controller()
    except ImportError:
        logger.info("zigpy not installed — permit_join unavailable")
        return False
    except Exception as exc:
        logger.warning("Zigbee controller start failed: %s", exc)
        return False
    try:
        await app.permit(time_s=duration_sec)
        logger.info("Zigbee permit-join opened for %ds", duration_sec)
        return True
    except Exception as exc:
        logger.warning("Zigbee permit_join failed: %s", exc)
        return False


async def shutdown() -> None:
    """Tear down the cached controller (called on module stop)."""
    global _controller
    async with _controller_lock:
        if _controller is None:
            return
        try:
            await _controller.shutdown()
        except Exception as exc:
            logger.debug("Zigbee controller shutdown error: %s", exc)
        _controller = None
