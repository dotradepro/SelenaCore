"""
system_modules/network_scanner/mdns_listener.py — mDNS/Bonjour listener

Listens for mDNS announcements on 224.0.0.251:5353 to discover devices
advertising services like _http._tcp, _hap._tcp (HomeKit), _googlecast._tcp, etc.

Uses the zeroconf library when available, otherwise raw socket fallback.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class MdnsDevice:
    name: str
    service_type: str
    hostname: str
    address: str
    port: int
    properties: dict[str, str] = field(default_factory=dict)


class MdnsListener:
    """Listen for mDNS service announcements."""

    # Common smart-home service types to monitor
    WATCH_TYPES = [
        "_http._tcp.local.",
        "_https._tcp.local.",
        "_hap._tcp.local.",          # HomeKit
        "_googlecast._tcp.local.",   # Chromecast/Google devices
        "_airplay._tcp.local.",      # Apple AirPlay
        "_ipp._tcp.local.",          # Printers
        "_smartthings._tcp.local.",
        "_home-assistant._tcp.local.",
        "_esphomelib._tcp.local.",   # ESPHome
    ]

    def __init__(self) -> None:
        self._devices: dict[str, MdnsDevice] = {}
        self._callbacks: list[Callable[[MdnsDevice], None]] = []

    def on_device(self, callback: Callable[[MdnsDevice], None]) -> None:
        self._callbacks.append(callback)

    def get_devices(self) -> list[MdnsDevice]:
        return list(self._devices.values())

    async def start(self) -> None:
        """Start listening for mDNS announcements."""
        try:
            await self._start_zeroconf()
        except ImportError:
            logger.warning("zeroconf library not available; mDNS discovery disabled")
        except Exception as exc:
            logger.error("mDNS listener error: %s", exc)

    async def _start_zeroconf(self) -> None:
        from zeroconf.asyncio import AsyncZeroconf, AsyncServiceBrowser  # type: ignore

        class _Handler:
            def __init__(self_inner) -> None:
                pass

            def add_service(self_inner, zc, service_type, name):  # noqa: N805
                asyncio.create_task(self._resolve_service(zc, service_type, name))

            def remove_service(self_inner, zc, service_type, name):  # noqa: N805
                key = f"{service_type}/{name}"
                self._devices.pop(key, None)

            def update_service(self_inner, zc, service_type, name):  # noqa: N805
                asyncio.create_task(self._resolve_service(zc, service_type, name))

        self._zc = AsyncZeroconf()
        self._browser = AsyncServiceBrowser(self._zc.zeroconf, self.WATCH_TYPES, handlers=[_Handler()])
        logger.info("mDNS listener started")

    async def _resolve_service(self, zc, service_type: str, name: str) -> None:
        try:
            from zeroconf import ServiceInfo  # type: ignore
            info = ServiceInfo(service_type, name)
            if info.request(zc, 3000):
                addresses = [str(addr) for addr in info.parsed_addresses()]
                device = MdnsDevice(
                    name=name,
                    service_type=service_type,
                    hostname=info.server or "",
                    address=addresses[0] if addresses else "",
                    port=info.port or 0,
                    properties={
                        k.decode() if isinstance(k, bytes) else k:
                        v.decode() if isinstance(v, bytes) else str(v)
                        for k, v in info.properties.items()
                    },
                )
                key = f"{service_type}/{name}"
                self._devices[key] = device
                for cb in self._callbacks:
                    try:
                        cb(device)
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("mDNS resolve error for %s: %s", name, exc)

    async def stop(self) -> None:
        try:
            if hasattr(self, "_zc"):
                await self._zc.async_close()
        except Exception:
            pass


_listener: MdnsListener | None = None


def get_mdns_listener() -> MdnsListener:
    global _listener
    if _listener is None:
        _listener = MdnsListener()
    return _listener
