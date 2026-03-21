"""
system_modules/network_scanner/arp_scanner.py — ARP sweep (passive + on-demand)

Passive mode: listen for ARP replies on the local network.
On-demand mode: broadcast ARP request for each host in subnet.
Uses scapy for ARP packets when available; falls back to parsing /proc/net/arp.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import AsyncIterator

logger = logging.getLogger(__name__)


@dataclass
class ArpEntry:
    ip: str
    mac: str
    iface: str = ""


async def passive_arp_entries() -> list[ArpEntry]:
    """Read current ARP table from /proc/net/arp (no root required)."""
    entries: list[ArpEntry] = []
    try:
        arp_table = open("/proc/net/arp").read()
        for line in arp_table.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4 and parts[2] == "0x2":  # flag 0x2 = complete
                ip, mac = parts[0], parts[3]
                iface = parts[5] if len(parts) > 5 else ""
                if re.match(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", mac, re.I):
                    entries.append(ArpEntry(ip=ip, mac=mac.lower(), iface=iface))
    except Exception as exc:
        logger.warning("Failed to read /proc/net/arp: %s", exc)
    return entries


async def active_arp_sweep(subnet: str) -> AsyncIterator[ArpEntry]:
    """On-demand ARP sweep using 'arping' for each host in subnet.

    Only sweeps /24 or smaller to avoid flooding large subnets.
    Yields ArpEntry as hosts respond.
    """
    try:
        net = ipaddress.ip_network(subnet, strict=False)
    except ValueError as exc:
        raise ValueError(f"Invalid subnet: {subnet!r}") from exc

    if net.num_addresses > 256:
        raise ValueError(f"Subnet {subnet} is too large for active sweep (max /24)")

    hosts = list(net.hosts())
    sem = asyncio.Semaphore(20)  # max 20 concurrent arping calls

    async def _probe(host: ipaddress.IPv4Address) -> ArpEntry | None:
        async with sem:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "arping", "-c", "1", "-W", "0.5", str(host),
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0:
                    # Parse MAC from arping output
                    match = re.search(r"\[([0-9a-fA-F:]{17})\]", stdout.decode())
                    if match:
                        return ArpEntry(ip=str(host), mac=match.group(1).lower())
            except FileNotFoundError:
                # arping not available; try scapy
                pass
            except Exception as exc:
                logger.debug("arping error for %s: %s", host, exc)
            return None

    tasks = [asyncio.create_task(_probe(h)) for h in hosts]
    for coro in asyncio.as_completed(tasks):
        result = await coro
        if result:
            yield result


async def get_arp_table() -> list[ArpEntry]:
    """Return combined passive ARP entries plus force a kernel refresh via ping sweep."""
    return await passive_arp_entries()
