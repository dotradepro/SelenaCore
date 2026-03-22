"""
system_modules/presence_detection/presence.py — PresenceDetector business logic

Detection methods (in priority order):
  1. ARP ping  — check if device IP responds (requires icmplib or arping)
  2. TCP port ping — fallback when ICMP not available (try port 7/22/80)
  3. Bluetooth RSSI — check if BT device is discoverable (requires bleak)
  4. Wi-Fi MAC — check ARP table for MAC address

User record:
  {
    "user_id": "uuid",
    "name": "Alice",
    "devices": [
      {"type": "ip", "address": "192.168.1.101"},
      {"type": "mac", "address": "aa:bb:cc:dd:ee:ff"},
      {"type": "bluetooth", "address": "AA:BB:CC:DD:EE:FF"},
    ],
    "state": "home" | "away" | "unknown",
    "last_seen": <ISO timestamp>,
    "confidence": 0.0-1.0,
  }

Events published:
  presence.home   — user arrived (was away)
  presence.away   — user left (was home)
  presence.scan   — periodic scan result (all users)
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Optional: icmplib
try:
    from icmplib import async_ping as icmplib_async_ping
    ICMPLIB_AVAILABLE = True
except ImportError:
    icmplib_async_ping = None  # type: ignore[assignment]
    ICMPLIB_AVAILABLE = False
    logger.info("icmplib not available — using TCP fallback for ARP ping")

# Optional: bleak (Bluetooth)
try:
    import bleak  # noqa: F401
    BLEAK_AVAILABLE = True
except ImportError:
    BLEAK_AVAILABLE = False
    logger.info("bleak not available — Bluetooth detection disabled")


# ── Low-level ping helpers ────────────────────────────────────────────────────

async def _icmp_ping(address: str, timeout: float = 2.0) -> bool:
    """ICMP ping via icmplib if available."""
    if not ICMPLIB_AVAILABLE or icmplib_async_ping is None:
        return False
    try:
        result = await icmplib_async_ping(address, count=1, timeout=timeout)
        return result.is_alive
    except Exception:
        return False


async def _tcp_ping(address: str, timeout: float = 2.0) -> bool:
    """TCP connect ping — try common ports. Returns True if any port responds."""
    ports = [80, 443, 22, 8080, 7]
    for port in ports:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(address, port),
                timeout=timeout,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
            continue
    return False


async def ping_ip(address: str, timeout: float = 2.0) -> bool:
    """Ping IP address — tries ICMP first, then TCP."""
    if ICMPLIB_AVAILABLE:
        result = await _icmp_ping(address, timeout)
        if result:
            return True
    # TCP fallback
    return await _tcp_ping(address, timeout)


def _read_arp_table() -> dict[str, str]:
    """Read /proc/net/arp and return {ip: mac} dict."""
    arp: dict[str, str] = {}
    try:
        with open("/proc/net/arp") as f:
            next(f)  # skip header
            for line in f:
                parts = line.split()
                if len(parts) >= 4:
                    ip, mac = parts[0], parts[3]
                    if mac not in ("00:00:00:00:00:00", ""):
                        arp[ip] = mac.lower()
    except Exception as exc:
        logger.debug(f"Could not read ARP table: {exc}")
    return arp


def mac_in_arp_table(mac: str) -> bool:
    """Check if a MAC address appears in ARP table."""
    target = mac.lower().replace("-", ":").strip()
    arp = _read_arp_table()
    return target in arp.values()


# ── PresenceDetector ─────────────────────────────────────────────────────────

class PresenceDetector:
    """Tracks user presence based on device IP/MAC/BT availability."""

    def __init__(
        self,
        publish_event_cb: Callable,
        scan_interval_sec: int = 60,
        away_threshold_sec: int = 180,
    ) -> None:
        self._publish_event = publish_event_cb
        self._scan_interval = scan_interval_sec
        self._away_threshold = away_threshold_sec
        self._users: dict[str, dict] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._scan_loop(), name="presence_scan")
        logger.info("PresenceDetector started")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── User management ────────────────────────────────────────────────────────

    def add_user(self, definition: dict) -> dict:
        user_id = definition["user_id"]
        if user_id not in self._users:
            self._users[user_id] = {
                **definition,
                "state": "unknown",
                "last_seen": None,
                "confidence": 0.0,
            }
        else:
            # Update devices list
            self._users[user_id].update({
                k: v for k, v in definition.items()
                if k in ("name", "devices")
            })
        return self._users[user_id]

    def remove_user(self, user_id: str) -> bool:
        if user_id in self._users:
            del self._users[user_id]
            return True
        return False

    def get_user(self, user_id: str) -> dict | None:
        return self._users.get(user_id)

    def list_users(self) -> list[dict]:
        return list(self._users.values())

    # ── Scan loop ──────────────────────────────────────────────────────────────

    async def _scan_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._scan_interval)
                await self._scan_all()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Presence scan error: {exc}")

    async def _scan_all(self) -> None:
        results = []
        for user_id, user in list(self._users.items()):
            detected = await self._detect_user(user)
            prev_state = user["state"]
            now = datetime.now(tz=timezone.utc).isoformat()

            if detected:
                user["last_seen"] = now
                user["confidence"] = 1.0
                if prev_state != "home":
                    user["state"] = "home"
                    await self._publish_event("presence.home", {
                        "user_id": user_id,
                        "name": user.get("name", ""),
                        "timestamp": now,
                    })
            else:
                # Only switch to away after threshold
                if user["last_seen"] is not None:
                    last_seen_dt = datetime.fromisoformat(user["last_seen"])
                    now_dt = datetime.now(tz=timezone.utc)
                    elapsed = (now_dt - last_seen_dt).total_seconds()
                    if elapsed >= self._away_threshold and prev_state == "home":
                        user["state"] = "away"
                        user["confidence"] = 0.0
                        await self._publish_event("presence.away", {
                            "user_id": user_id,
                            "name": user.get("name", ""),
                            "timestamp": now,
                        })
                elif prev_state == "unknown":
                    user["state"] = "away"

            results.append({"user_id": user_id, "state": user["state"]})

        await self._publish_event("presence.scan", {
            "users": results,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        })

    async def _detect_user(self, user: dict) -> bool:
        """Return True if any device for the user is reachable."""
        devices = user.get("devices", [])
        checks = [self._check_device(d) for d in devices]
        if not checks:
            return False
        results = await asyncio.gather(*checks, return_exceptions=True)
        return any(r is True for r in results)

    async def _check_device(self, device: dict) -> bool:
        dtype = device.get("type", "")
        address = device.get("address", "")
        if not address:
            return False

        if dtype == "ip":
            return await ping_ip(address)
        elif dtype == "mac":
            return mac_in_arp_table(address)
        elif dtype == "bluetooth":
            return await self._check_bluetooth(address)
        return False

    async def _check_bluetooth(self, bt_address: str) -> bool:
        """Check if a Bluetooth device is discoverable (requires bleak)."""
        if not BLEAK_AVAILABLE:
            return False
        try:
            import bleak
            devices = await bleak.BleakScanner.discover(timeout=3.0)
            return any(d.address.lower() == bt_address.lower() for d in devices)
        except Exception as exc:
            logger.debug(f"BT scan failed: {exc}")
            return False

    # ── Manual trigger ─────────────────────────────────────────────────────────

    async def trigger_scan_now(self) -> list[dict]:
        await self._scan_all()
        return self.list_users()

    # ── Status ─────────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        home_count = sum(1 for u in self._users.values() if u["state"] == "home")
        return {
            "users_total": len(self._users),
            "users_home": home_count,
            "users_away": sum(1 for u in self._users.values() if u["state"] == "away"),
            "scan_interval_sec": self._scan_interval,
            "away_threshold_sec": self._away_threshold,
            "icmplib_available": ICMPLIB_AVAILABLE,
            "bluetooth_available": BLEAK_AVAILABLE,
        }
