"""
system_modules/device_control/drivers/tuya_local.py — Tuya local LAN driver.

Uses tinytuya in persistent-socket mode. Each driver instance owns one TCP
socket to the device. ``stream_events()`` blocks on ``device.receive()`` and
yields whenever Tuya announces a DPS change (physical button press, app
control, scheduler, etc.) — no polling.

Logical state shape (returned to module + stored in DB):
    {"on": bool, "brightness": int | None, "colour_temp": int | None}

``device.meta["tuya"]`` schema:
    {
        "device_id":  str,   # 22-char Tuya device id
        "local_key":  str,   # 16-char AES key from Tuya cloud
        "ip":         str,   # LAN IP
        "version":    str,   # protocol version, "3.3" / "3.4" / "3.5"
        "dps_map":    {      # logical key → DPS index (string)
            "on":         "1",
            "brightness": "2",
            "colour_temp": "3"
        }
    }
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

from .base import DeviceDriver, DriverError

logger = logging.getLogger(__name__)

# Default DPS mapping for the most common Tuya switch / dimmer profile.
# User can override per-device via meta.tuya.dps_map.
_DEFAULT_DPS_MAP: dict[str, str] = {"on": "1"}


class TuyaLocalDriver(DeviceDriver):
    protocol = "tuya_local"

    def __init__(self, device_id: str, meta: dict[str, Any]) -> None:
        super().__init__(device_id, meta)
        cfg = (meta or {}).get("tuya") or {}
        self._tuya_id: str = cfg.get("device_id", "")
        self._local_key: str = cfg.get("local_key", "")
        self._ip: str = cfg.get("ip", "")
        self._version: float = float(cfg.get("version", "3.3"))
        self._dps_map: dict[str, str] = dict(cfg.get("dps_map") or _DEFAULT_DPS_MAP)
        self._reverse_map: dict[str, str] = {v: k for k, v in self._dps_map.items()}
        self._dev: Any = None  # tinytuya.OutletDevice
        self._lock = asyncio.Lock()

    # ── Helpers ──────────────────────────────────────────────────────────

    def _build_dev(self):
        try:
            import tinytuya  # type: ignore
        except ImportError as exc:
            raise DriverError("tinytuya not installed") from exc
        if not (self._tuya_id and self._local_key and self._ip):
            raise DriverError(
                f"Tuya device {self.device_id}: missing device_id/local_key/ip"
            )
        d = tinytuya.OutletDevice(
            dev_id=self._tuya_id,
            address=self._ip,
            local_key=self._local_key,
            version=self._version,
        )
        d.set_socketPersistent(True)
        d.set_socketTimeout(15)
        d.set_socketRetryLimit(0)  # we handle retries at the watcher level
        return d

    def _dps_to_logical(self, dps: dict[str, Any] | None) -> dict[str, Any]:
        """Translate raw DPS dict from tinytuya into logical state."""
        if not dps:
            return {}
        out: dict[str, Any] = {}
        for raw_key, raw_val in dps.items():
            key = str(raw_key)
            logical = self._reverse_map.get(key)
            if logical:
                out[logical] = raw_val
        return out

    def _logical_to_dps(self, state: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in state.items():
            dps_key = self._dps_map.get(k)
            if dps_key is not None:
                out[dps_key] = v
        return out

    # ── DeviceDriver ─────────────────────────────────────────────────────

    async def connect(self) -> dict[str, Any]:
        async with self._lock:
            if self._dev is None:
                self._dev = await asyncio.to_thread(self._build_dev)
            try:
                status = await asyncio.to_thread(self._dev.status)
            except Exception as exc:
                self._dev = None
                raise DriverError(f"Tuya connect failed: {exc}") from exc
        if not isinstance(status, dict):
            raise DriverError(f"Tuya status returned non-dict: {status!r}")
        if "Error" in status or "err" in status:
            raise DriverError(f"Tuya status error: {status}")
        return self._dps_to_logical(status.get("dps"))

    async def disconnect(self) -> None:
        async with self._lock:
            d = self._dev
            self._dev = None
        if d is not None:
            try:
                await asyncio.to_thread(d.set_socketPersistent, False)
            except Exception:
                pass
            try:
                # tinytuya >=1.13 exposes close()
                close = getattr(d, "close", None)
                if callable(close):
                    await asyncio.to_thread(close)
            except Exception:
                pass

    async def set_state(self, state: dict[str, Any]) -> None:
        dps = self._logical_to_dps(state)
        if not dps:
            return
        if self._dev is None:
            await self.connect()
        try:
            await asyncio.to_thread(
                lambda: self._dev.set_multiple_values(dps, nowait=False)
            )
        except Exception as exc:
            raise DriverError(f"Tuya set_state failed: {exc}") from exc

    async def get_state(self) -> dict[str, Any]:
        if self._dev is None:
            return await self.connect()
        try:
            status = await asyncio.to_thread(self._dev.status)
        except Exception as exc:
            raise DriverError(f"Tuya get_state failed: {exc}") from exc
        return self._dps_to_logical((status or {}).get("dps"))

    async def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        if self._dev is None:
            await self.connect()
        # tinytuya.receive() is a blocking sync call. Run each call in a
        # thread so the event loop stays responsive.
        while True:
            try:
                payload = await asyncio.to_thread(self._dev.receive)
            except Exception as exc:
                raise DriverError(f"Tuya socket dropped: {exc}") from exc
            if payload is None:
                # tinytuya returns None on heartbeat / nothing — keep waiting.
                continue
            if not isinstance(payload, dict):
                continue
            if "Error" in payload or "err" in payload:
                raise DriverError(f"Tuya stream error: {payload}")
            dps = payload.get("dps") or payload.get("data", {}).get("dps")
            logical = self._dps_to_logical(dps) if dps else {}
            if logical:
                yield logical
