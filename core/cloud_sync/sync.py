"""
core/cloud_sync/sync.py — CloudSync background task:
  - heartbeat ping to SmartHome LK platform every 60 seconds
  - long-poll command receiver with ACK
  - integrity event reporting

Architecture:
  - Two concurrent asyncio tasks: heartbeat_loop + command_loop
  - HMAC-SHA256 request signing (X-Selena-Signature header)
  - Exponential back-off on connection loss (max 5 min)
  - Publishes sync.* events to the internal Event Bus
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

from core.config import get_settings
from core.eventbus.bus import get_event_bus
from core.eventbus.types import (
    SYNC_COMMAND_RECEIVED,
    SYNC_CONNECTION_LOST,
    SYNC_CONNECTION_RESTORED,
)

logger = logging.getLogger(__name__)

# Heartbeat interval (seconds)
HEARTBEAT_INTERVAL = 60
# Long-poll timeout for command endpoint
LONG_POLL_TIMEOUT = 55
# Initial retry delay; doubles each attempt, capped at MAX_RETRY_DELAY
INITIAL_RETRY_DELAY = 5
MAX_RETRY_DELAY = 300


def _hmac_signature(body: bytes, secret: str) -> str:
    """Produce HMAC-SHA256 hex signature for a request body."""
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _platform_headers(body: bytes, device_hash: str) -> dict[str, str]:
    """Build auth headers for platform requests."""
    return {
        "Content-Type": "application/json",
        "X-Selena-Device": device_hash,
        "X-Selena-Signature": _hmac_signature(body, device_hash),
        "X-Selena-Timestamp": str(int(time.time())),
    }


def _collect_system_state() -> dict[str, Any]:
    """Gather current system state for heartbeat payload."""
    import platform as _platform

    try:
        import psutil

        cpu_percent = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        cpu_temp: float | None = None
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                first_sensor = next(iter(temps.values()))
                if first_sensor:
                    cpu_temp = first_sensor[0].current
        except Exception:
            pass
        hw = {
            "cpu_percent": cpu_percent,
            "ram_used_mb": ram.used // (1024 * 1024),
            "ram_total_mb": ram.total // (1024 * 1024),
            "disk_used_gb": disk.used / (1024 ** 3),
            "disk_total_gb": disk.total / (1024 ** 3),
            "cpu_temp": cpu_temp,
        }
    except ImportError:
        hw = {}

    settings = get_settings()
    return {
        "version": "0.3.0-beta",
        "platform": _platform.machine(),
        "hostname": _platform.node(),
        "hardware": hw,
        "timestamp": time.time(),
    }


class CloudSync:
    """Manages background tasks for platform connectivity."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._connected: bool = False
        self._tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()

    @property
    def _base_url(self) -> str:
        return self._settings.platform_api_url.rstrip("/")

    @property
    def _device_hash(self) -> str:
        return self._settings.platform_device_hash

    @property
    def _mock(self) -> bool:
        return self._settings.mock_platform or not self._device_hash

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(LONG_POLL_TIMEOUT + 10))

    async def start(self) -> None:
        if self._mock:
            logger.info("CloudSync: mock mode — no real platform connection")
            return
        self._stop_event.clear()
        self._tasks = [
            asyncio.create_task(self._heartbeat_loop(), name="cloud-heartbeat"),
            asyncio.create_task(self._command_loop(), name="cloud-commands"),
        ]
        logger.info("CloudSync started (platform=%s)", self._base_url)

    async def stop(self) -> None:
        self._stop_event.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("CloudSync stopped")

    # ------------------------------------------------------------------ #
    # Heartbeat                                                           #
    # ------------------------------------------------------------------ #

    async def _heartbeat_loop(self) -> None:
        retry_delay = INITIAL_RETRY_DELAY
        async with self._make_client() as client:
            while not self._stop_event.is_set():
                try:
                    await self._send_heartbeat(client)
                    if not self._connected:
                        self._connected = True
                        retry_delay = INITIAL_RETRY_DELAY
                        await self._publish(SYNC_CONNECTION_RESTORED, {})
                        logger.info("CloudSync: connection restored")
                    await asyncio.wait_for(
                        asyncio.shield(self._stop_event.wait()),
                        timeout=HEARTBEAT_INTERVAL,
                    )
                except asyncio.TimeoutError:
                    pass  # normal — sleep expired, send next heartbeat
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    if self._connected:
                        self._connected = False
                        await self._publish(SYNC_CONNECTION_LOST, {"error": str(e)})
                        logger.warning("CloudSync: heartbeat error: %s", e)
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)

    async def _send_heartbeat(self, client: httpx.AsyncClient) -> None:
        payload = _collect_system_state()
        body = json.dumps(payload).encode()
        headers = _platform_headers(body, self._device_hash)
        resp = await client.post(
            f"{self._base_url}/devices/{self._device_hash}/heartbeat",
            content=body,
            headers=headers,
        )
        resp.raise_for_status()
        logger.debug("CloudSync heartbeat OK (status=%d)", resp.status_code)

    # ------------------------------------------------------------------ #
    # Long-poll command receiver                                           #
    # ------------------------------------------------------------------ #

    async def _command_loop(self) -> None:
        retry_delay = INITIAL_RETRY_DELAY
        from core.cloud_sync.commands import dispatch_command

        async with self._make_client() as client:
            while not self._stop_event.is_set():
                try:
                    command = await self._poll_command(client)
                    if command:
                        retry_delay = INITIAL_RETRY_DELAY
                        cmd_id = command.get("command_id", "unknown")
                        await self._publish(SYNC_COMMAND_RECEIVED, command)
                        success = await dispatch_command(command)
                        await self._ack_command(client, cmd_id, success)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.warning("CloudSync: command poll error: %s", e)
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)

    async def _poll_command(self, client: httpx.AsyncClient) -> dict | None:
        """Long-poll the platform for pending commands. Returns command dict or None."""
        body = b""
        headers = _platform_headers(body, self._device_hash)
        headers["Content-Type"] = "application/json"
        try:
            resp = await client.get(
                f"{self._base_url}/devices/{self._device_hash}/commands/next",
                headers=headers,
                timeout=httpx.Timeout(LONG_POLL_TIMEOUT + 5),
            )
            if resp.status_code == 204:
                return None  # no command pending
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            return None

    async def _ack_command(
        self, client: httpx.AsyncClient, command_id: str, success: bool
    ) -> None:
        payload = {"command_id": command_id, "status": "ok" if success else "error"}
        body = json.dumps(payload).encode()
        headers = _platform_headers(body, self._device_hash)
        try:
            resp = await client.post(
                f"{self._base_url}/devices/{self._device_hash}/commands/{command_id}/ack",
                content=body,
                headers=headers,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning("CloudSync: ack failed for %s: %s", command_id, e)

    # ------------------------------------------------------------------ #
    # Integrity event reporting                                            #
    # ------------------------------------------------------------------ #

    async def report_integrity_event(
        self, reason: str, changed_files: list[dict]
    ) -> None:
        """Called by the integrity agent to report violations to the platform."""
        if self._mock:
            return
        payload = {
            "event": "integrity_violation",
            "reason": reason,
            "changed_files": changed_files,
            "timestamp": time.time(),
        }
        body = json.dumps(payload).encode()
        headers = _platform_headers(body, self._device_hash)
        retry_delay = INITIAL_RETRY_DELAY
        for attempt in range(5):
            try:
                async with self._make_client() as client:
                    resp = await client.post(
                        f"{self._base_url}/devices/{self._device_hash}/events",
                        content=body,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    logger.info("CloudSync: integrity event reported")
                    return
            except Exception as e:
                logger.warning(
                    "CloudSync: integrity report attempt %d failed: %s", attempt + 1, e
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)

    async def _publish(self, event_type: str, payload: dict) -> None:
        try:
            bus = get_event_bus()
            await bus.publish(type=event_type, source="core.cloud_sync", payload=payload)
        except Exception as e:
            logger.debug("CloudSync: event bus publish failed: %s", e)


# Singleton
_sync: CloudSync | None = None


def get_cloud_sync() -> CloudSync:
    global _sync
    if _sync is None:
        _sync = CloudSync()
    return _sync
