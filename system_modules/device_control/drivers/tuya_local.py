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
import time
from typing import Any, AsyncGenerator

from .base import DeviceDriver, DriverError

logger = logging.getLogger(__name__)

# Default DPS mapping for the most common Tuya switch / dimmer profile.
# User can override per-device via meta.tuya.dps_map.
_DEFAULT_DPS_MAP: dict[str, str] = {"on": "1"}

# Standard Tuya plug metering DPS — universal across category "cz" (sockets):
#   DPS 18 = current  in mA
#   DPS 19 = power    in deciwatts (W * 10)
#   DPS 20 = voltage  in decivolts (V * 10)
# These are not used by switch / dimmer / light profiles, so it is safe to
# parse them unconditionally and expose via consume_metering().
_METERING_DPS_CURRENT = "18"
_METERING_DPS_POWER = "19"
_METERING_DPS_VOLTAGE = "20"


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
        # Latest power-metering snapshot parsed from a status frame.
        # Drained one-shot by consume_metering() after the watcher publishes
        # device.power_reading on the bus.
        self._last_metering: dict[str, float] | None = None

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
        # Short receive timeout (1s) lets stream_events() yield the lock
        # quickly so set_state() / get_state() can interleave without
        # waiting for the watcher to finish a long blocking recv. tinytuya
        # returns None on timeout (treated as heartbeat). With this value
        # the worst-case command latency is ~1s instead of 15s.
        d.set_socketTimeout(1)
        # Tuya v3.4/3.5 negotiate a session key on the first command — that
        # handshake takes 2-3 inner retries before it stabilises. Setting
        # retry=0 made every status() on a v3.4 device fail with Err 905
        # "Device Unreachable". Leave the tinytuya default (5) so the
        # handshake succeeds. The watcher's exponential backoff still
        # protects against truly-offline devices.
        d.set_socketRetryLimit(5)
        return d

    def _dps_to_logical(self, dps: dict[str, Any] | None) -> dict[str, Any]:
        """Translate raw DPS dict from tinytuya into logical state.

        Side effect: updates ``self._last_metering`` whenever a status
        frame contains energy-meter DPS (18 / 19 / 20). Metering is NOT
        merged into the logical state — it is exposed via
        ``consume_metering()`` so the watcher can publish it as a
        separate ``device.power_reading`` bus event.
        """
        if not dps:
            return {}
        out: dict[str, Any] = {}
        for raw_key, raw_val in dps.items():
            key = str(raw_key)
            logical = self._reverse_map.get(key)
            if logical:
                out[logical] = raw_val
        metering = self._extract_metering(dps)
        if metering is not None:
            self._last_metering = metering
        return out

    @staticmethod
    def _extract_metering(dps: dict[str, Any]) -> dict[str, float] | None:
        """Parse Tuya plug metering DPS into watts / volts / amps.

        Returns ``None`` if the frame contains none of DPS 18/19/20 (i.e.
        the device is not a metered plug, or this frame is just an on/off
        update). Returns a dict with whichever metering keys were present
        in the frame.
        """
        if not (
            _METERING_DPS_CURRENT in dps
            or _METERING_DPS_POWER in dps
            or _METERING_DPS_VOLTAGE in dps
        ):
            return None
        out: dict[str, float] = {}
        try:
            if _METERING_DPS_POWER in dps:
                out["watts"] = float(dps[_METERING_DPS_POWER]) / 10.0
            if _METERING_DPS_VOLTAGE in dps:
                out["volts"] = float(dps[_METERING_DPS_VOLTAGE]) / 10.0
            if _METERING_DPS_CURRENT in dps:
                out["amps"] = float(dps[_METERING_DPS_CURRENT]) / 1000.0
        except (TypeError, ValueError):
            return None
        return out or None

    def consume_metering(self) -> dict[str, float] | None:
        """One-shot read of the latest metering snapshot. See base class."""
        m = self._last_metering
        self._last_metering = None
        return m

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
        # Serialize against stream_events()'s receive() loop. Without this,
        # tinytuya v3.4 frequently corrupts its internal session-key state
        # and returns Err 914 "Check device key or version".
        async with self._lock:
            try:
                await asyncio.to_thread(
                    lambda: self._dev.set_multiple_values(dps, nowait=False)
                )
            except Exception as exc:
                raise DriverError(f"Tuya set_state failed: {exc}") from exc

    async def get_state(self) -> dict[str, Any]:
        if self._dev is None:
            return await self.connect()
        async with self._lock:
            try:
                status = await asyncio.to_thread(self._dev.status)
            except Exception as exc:
                raise DriverError(f"Tuya get_state failed: {exc}") from exc
        return self._dps_to_logical((status or {}).get("dps"))

    async def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        if self._dev is None:
            await self.connect()
        # tinytuya error codes that mean "the connection is dead, reconnect":
        #   901 ERR_CONNECT, 905 ERR_OFFLINE, 914 ERR_KEY_OR_VER
        # Everything else (notably 904 ERR_PAYLOAD "Unexpected Payload from
        # Device" — emitted on benign frames from device22 / mixed protocol
        # versions) is non-fatal: tinytuya updates its internal state and
        # the next receive() returns clean data. Reconnecting on those just
        # produces an endless loop of "offline → reconnect → same error".
        _FATAL_ERRS = {"901", "905", "914", 901, 905, 914}
        # Active poll fallback: many Tuya plugs don't push status frames on
        # their own (only respond to status() requests). After this many
        # seconds without good data, fall back to an active status() poll
        # so metering data still flows. Kept short (5s) so power changes
        # — including load drops to 0 W when the user unplugs a lamp —
        # surface quickly. tinytuya status() is one cheap LAN round-trip.
        _POLL_INTERVAL_SEC = 5.0
        consecutive_errors = 0
        last_good_ts = time.monotonic()
        # tinytuya.receive() is a blocking sync call. Run each call in a
        # thread so the event loop stays responsive. The lock is acquired
        # PER ITERATION so set_state()/get_state() can interleave between
        # receives instead of waiting for the watcher to finish.
        while True:
            # Active poll fallback: if we haven't seen any good DPS frame
            # for a while, the device probably doesn't push status on its
            # own. Ask explicitly so metering data keeps flowing — runs
            # whether the previous tick was a heartbeat, an error, or a
            # real frame.
            if time.monotonic() - last_good_ts >= _POLL_INTERVAL_SEC:
                async with self._lock:
                    try:
                        status = await asyncio.to_thread(self._dev.status)
                    except Exception as exc:
                        raise DriverError(
                            f"Tuya status poll failed: {exc}"
                        ) from exc
                last_good_ts = time.monotonic()
                if isinstance(status, dict) and "Error" not in status and "err" not in status:
                    poll_dps = status.get("dps") or status.get("data", {}).get("dps")
                    if poll_dps:
                        poll_logical = self._dps_to_logical(poll_dps)
                        if poll_logical:
                            yield poll_logical
            async with self._lock:
                try:
                    payload = await asyncio.to_thread(self._dev.receive)
                except Exception as exc:
                    raise DriverError(f"Tuya socket dropped: {exc}") from exc
            if payload is None:
                # tinytuya returns None on heartbeat / nothing — keep waiting.
                # Brief yield so the lock can be picked up by set_state.
                consecutive_errors = 0
                await asyncio.sleep(0)
                continue
            if not isinstance(payload, dict):
                consecutive_errors = 0
                continue
            if "Error" in payload or "err" in payload:
                err_code = payload.get("Err") or payload.get("err")
                if err_code in _FATAL_ERRS:
                    raise DriverError(f"Tuya stream error: {payload}")
                consecutive_errors += 1
                if consecutive_errors >= 30:
                    # Backstop: 30 errors in a row with no good frames
                    # means something is genuinely wrong. Force reconnect.
                    raise DriverError(
                        f"Tuya stream stuck after {consecutive_errors} "
                        f"consecutive errors: {payload}"
                    )
                # Log first occurrence at info, the rest at debug to avoid
                # flooding the log with the same transient hiccup.
                if consecutive_errors == 1:
                    logger.info(
                        "tuya_local %s: transient receive error (will keep "
                        "listening): %s", self.device_id, payload,
                    )
                else:
                    logger.debug(
                        "tuya_local %s: transient receive error #%d: %s",
                        self.device_id, consecutive_errors, payload,
                    )
                await asyncio.sleep(0)
                continue
            consecutive_errors = 0
            dps = payload.get("dps") or payload.get("data", {}).get("dps")
            logical = self._dps_to_logical(dps) if dps else {}
            if logical or dps:
                last_good_ts = time.monotonic()
            if logical:
                yield logical
