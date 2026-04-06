"""
system_modules/device_watchdog/watchdog.py — device monitoring business logic

Periodically checks availability of all devices in Device Registry.
Supports: ICMP ping (icmplib, no root required), MQTT/Zigbee last_seen timeout.
Publishes device.online / device.offline events on status change.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

try:
    from icmplib import async_ping as icmplib_ping
    ICMPLIB_AVAILABLE = True
except ImportError:
    icmplib_ping = None  # type: ignore[assignment]
    ICMPLIB_AVAILABLE = False
    logger.warning("icmplib not installed — ICMP ping disabled, using TCP fallback")

CORE_API_BASE = "http://localhost/api/v1"
MODULE_NAME = "device-watchdog"


@dataclass
class DeviceStatus:
    device_id: str
    is_online: bool = True
    fail_streak: int = 0
    offline_since: str | None = None


@dataclass
class WatchdogConfig:
    check_interval_sec: int = 60
    ping_timeout_sec: float = 2.0
    mqtt_timeout_sec: int = 120
    protocol_timeout_sec: int = 300
    offline_threshold: int = 3
    notify_on_offline: bool = True


class DeviceWatchdog:
    """Monitors all registered devices and publishes online/offline events."""

    def __init__(
        self,
        publish_callback: Callable,
        get_devices_callback: Callable,
        update_device_callback: Callable,
        config: dict | None = None,
    ) -> None:
        self._publish = publish_callback
        self._get_devices = get_devices_callback
        self._update_device = update_device_callback
        self._cfg = WatchdogConfig(**(config or {}))
        self._statuses: dict[str, DeviceStatus] = {}
        self._task: asyncio.Task | None = None

    def update_config(self, config: dict) -> None:
        for key, value in config.items():
            if hasattr(self._cfg, key):
                setattr(self._cfg, key, value)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._check_loop(), name="watchdog_loop")
        logger.info(f"DeviceWatchdog started (interval={self._cfg.check_interval_sec}s)")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("DeviceWatchdog stopped")

    async def check_now(self) -> dict:
        """Manual scan trigger. Returns scan summary."""
        return await self._run_check()

    async def on_protocol_heartbeat(self, payload: dict) -> None:
        """Called when device.protocol_heartbeat event received."""
        device_id = payload.get("device_id")
        if not device_id:
            return
        # Update last_seen via Core API — heartbeat = device is alive
        await self._update_device(device_id, {
            "protocol_last_seen": datetime.now(tz=timezone.utc).isoformat()
        })
        # Recover if previously marked offline
        status = self._statuses.get(device_id)
        if status and not status.is_online:
            await self._mark_online(device_id, "heartbeat")

    # ── Internal ────────────────────────────────────────────────────────────

    async def _check_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._cfg.check_interval_sec)
                await self._run_check()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Watchdog check error: {exc}")

    async def _run_check(self) -> dict:
        start_ts = datetime.now(tz=timezone.utc)
        try:
            devices = await self._get_devices()
        except Exception as exc:
            logger.error(f"Failed to get devices: {exc}")
            return {"checked": 0, "online": 0, "offline": 0, "error": str(exc)}

        online_count = 0
        offline_count = 0

        tasks = [self._check_device(d) for d in devices]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Device check exception: {result}")
            elif result is True:
                online_count += 1
            else:
                offline_count += 1

        duration_ms = int(
            (datetime.now(tz=timezone.utc) - start_ts).total_seconds() * 1000
        )
        await self._publish("device.watchdog_scan", {
            "checked": len(devices),
            "online": online_count,
            "offline": offline_count,
            "duration_ms": duration_ms,
        })
        return {"checked": len(devices), "online": online_count, "offline": offline_count}

    async def _check_device(self, device: dict) -> bool:
        device_id = device.get("device_id") or device.get("id", "")
        if not device_id:
            return True

        was_online = self._statuses.get(device_id, DeviceStatus(device_id)).is_online
        is_online = await self._ping(device)

        if device_id not in self._statuses:
            # Always start as «online» so the first real offline detection fires the event
            self._statuses[device_id] = DeviceStatus(device_id, is_online=True)

        status = self._statuses[device_id]

        if is_online:
            status.fail_streak = 0
            if not was_online:
                await self._mark_online(device_id, "ping_recovered")
            status.is_online = True
        else:
            status.fail_streak += 1
            if status.fail_streak >= self._cfg.offline_threshold and was_online:
                await self._mark_offline(device_id, device)
                status.is_online = False

        return status.is_online

    async def _ping(self, device: dict) -> bool:
        protocol = device.get("protocol", "unknown")
        meta = device.get("meta", {})

        if protocol in ("wifi", "http"):
            ip = meta.get("ip_address")
            if not ip:
                return False
            return await self._icmp_ping(ip)

        if protocol == "mqtt":
            last_seen = meta.get("mqtt_last_seen")
            if not last_seen:
                return False
            return self._within_timeout(last_seen, self._cfg.mqtt_timeout_sec)

        if protocol in ("zigbee", "zwave"):
            last_seen = meta.get("protocol_last_seen")
            if not last_seen:
                return True  # unknown → assume online
            return self._within_timeout(last_seen, self._cfg.protocol_timeout_sec)

        return True  # unknown protocol — don't penalise

    async def _icmp_ping(self, host: str) -> bool:
        if ICMPLIB_AVAILABLE:
            try:
                result = await icmplib_ping(
                    host,
                    count=1,
                    timeout=self._cfg.ping_timeout_sec,
                    privileged=False,
                )
                return result.is_alive
            except Exception as exc:
                logger.debug(f"ICMP ping failed for {host}: {exc}")
                return False

        # TCP fallback — try port 80
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, 80),
                timeout=self._cfg.ping_timeout_sec,
            )
            writer.close()
            return True
        except Exception:
            return False

    @staticmethod
    def _within_timeout(iso_timestamp: str, threshold_sec: int) -> bool:
        try:
            ts = datetime.fromisoformat(iso_timestamp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(tz=timezone.utc) - ts).total_seconds()
            return elapsed < threshold_sec
        except ValueError:
            return False

    async def _mark_offline(self, device_id: str, device: dict) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._statuses[device_id].offline_since = now
        logger.warning(f"Device offline: {device_id}")
        if not self._cfg.notify_on_offline:
            return
        await self._update_device(device_id, {
            "watchdog_online": False,
            "watchdog_last_seen": now,
        })
        await self._publish("device.offline", {
            "device_id": device_id,
            "device_name": device.get("name", ""),
            "protocol": device.get("protocol", ""),
            "ip": device.get("meta", {}).get("ip_address"),
            "offline_since": now,
        })

    async def _mark_online(self, device_id: str, reason: str) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        if device_id in self._statuses:
            self._statuses[device_id].offline_since = None
        logger.info(f"Device online: {device_id} (reason={reason})")
        await self._update_device(device_id, {
            "watchdog_online": True,
            "watchdog_last_seen": now,
        })
        await self._publish("device.online", {
            "device_id": device_id,
            "device_name": "",
            "protocol": "",
        })

    def get_status_summary(self) -> dict:
        """Return counts for widget display."""
        total = len(self._statuses)
        online = sum(1 for s in self._statuses.values() if s.is_online)
        return {"total": total, "online": online, "offline": total - online}
