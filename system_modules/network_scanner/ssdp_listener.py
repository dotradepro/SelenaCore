"""
system_modules/network_scanner/ssdp_listener.py — SSDP/UPnP device discovery

Listens for SSDP multicast announcements (239.255.255.250:1900) and sends
M-SEARCH probes to actively discover UPnP/DLNA devices.
"""
from __future__ import annotations

import asyncio
import logging
import re
import socket
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
    "MAN: \"ssdp:discover\"\r\n"
    "MX: 3\r\n"
    "ST: ssdp:all\r\n"
    "\r\n"
)


@dataclass
class SsdpDevice:
    location: str            # Description XML URL
    usn: str                 # Unique Service Name
    st: str                  # Service/device type
    server: str = ""
    address: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def _parse_ssdp(data: str) -> dict[str, str]:
    """Parse SSDP HTTP-like headers into a dict."""
    result: dict[str, str] = {}
    for line in data.splitlines()[1:]:
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip().upper()] = val.strip()
    return result


class SsdpListener:
    def __init__(self) -> None:
        self._devices: dict[str, SsdpDevice] = {}
        self._callbacks: list[Callable[[SsdpDevice], None]] = []
        self._running = False

    def on_device(self, callback: Callable[[SsdpDevice], None]) -> None:
        self._callbacks.append(callback)

    def get_devices(self) -> list[SsdpDevice]:
        return list(self._devices.values())

    def _register(self, headers: dict[str, str], address: str) -> None:
        usn = headers.get("USN", "")
        location = headers.get("LOCATION", "")
        st = headers.get("ST") or headers.get("NT", "")
        if not usn:
            return
        device = SsdpDevice(
            location=location,
            usn=usn,
            st=st,
            server=headers.get("SERVER", ""),
            address=address,
        )
        if usn not in self._devices:
            self._devices[usn] = device
            for cb in self._callbacks:
                try:
                    cb(device)
                except Exception:
                    pass

    async def listen(self, timeout: float = 10.0) -> None:
        """Listen for SSDP packets for `timeout` seconds."""
        loop = asyncio.get_event_loop()

        # Create multicast UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        sock.bind(("", SSDP_PORT))

        group = socket.inet_aton(SSDP_ADDR)
        mreq = group + socket.inet_aton("0.0.0.0")
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.setblocking(False)

        # Also send M-SEARCH probe
        try:
            search_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            search_sock.settimeout(1)
            search_sock.sendto(SSDP_MSEARCH.encode(), (SSDP_ADDR, SSDP_PORT))
            search_sock.close()
        except Exception as exc:
            logger.debug("SSDP M-SEARCH error: %s", exc)

        deadline = loop.time() + timeout
        try:
            while loop.time() < deadline:
                try:
                    data, addr = await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: sock.recvfrom(4096)),
                        timeout=1.0,
                    )
                    headers = _parse_ssdp(data.decode(errors="replace"))
                    self._register(headers, addr[0])
                except asyncio.TimeoutError:
                    continue
                except Exception as exc:
                    logger.debug("SSDP recv error: %s", exc)
        finally:
            sock.close()

    async def active_search(self, timeout: float = 6.0) -> list[SsdpDevice]:
        """Send M-SEARCH and collect responses."""
        await self.listen(timeout=timeout)
        return self.get_devices()


_listener: SsdpListener | None = None


def get_ssdp_listener() -> SsdpListener:
    global _listener
    if _listener is None:
        _listener = SsdpListener()
    return _listener
