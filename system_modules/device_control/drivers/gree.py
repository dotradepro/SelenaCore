"""
system_modules/device_control/drivers/gree.py — Gree / Pular WiFi A/C driver.

Speaks the Gree LAN protocol (UDP/7000, AES-ECB) via the open-source
``greeclimate`` library — same protocol used by Pular GWH12AGB-I-R32 and
the rest of the Gree-OEM split-AC family. Local-only, no cloud.

Logical state shape (returned to module + stored in DB):

    {
        "on":            bool,
        "mode":          "auto" | "cool" | "dry" | "fan" | "heat",
        "target_temp":   int,    # °C, clamped to [16, 30]
        "current_temp":  int,    # °C, read-only (sensor)
        "fan_speed":     "auto" | "low" | "medium_low" | "medium" |
                         "medium_high" | "high",
        "swing_v":       "off" | "full" | "fixed_top" | "fixed_middle_top"
                         | "fixed_middle" | "fixed_middle_bottom" | "fixed_bottom"
                         | "swing_bottom" | "swing_middle" | "swing_top",
        "swing_h":       "off" | "full" | "left" | "left_center" | "center"
                         | "right_center" | "right",
        "sleep":         bool,
        "turbo":         bool,
        "light":         bool,
        "eco":           bool,
        "health":        bool,
        "quiet":         bool,
    }

``device.meta["gree"]`` schema:

    {
        "ip":      str,        # LAN IP (required)
        "mac":     str,        # MAC address (required)
        "name":    str,        # display name from discovery (optional)
        "port":    int,        # default 7000
        "key":     str | None, # AES key — populated by bind(), persisted by
                               # the watcher's _persist_meta() helper
        "brand":   str,        # "gree" | "pular" | …
        "model":   str,        # OEM model id, e.g. "GWH12AGB"
    }
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

from .base import DeviceDriver, DriverError

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

POLL_INTERVAL_SECONDS = 5.0
TEMP_MIN = 16
TEMP_MAX = 30
DEFAULT_PORT = 7000

#: Capabilities advertised on devices created via the Gree wizard. The
#: voice-intent resolver in device-control filters by these strings.
AC_CAPABILITIES = [
    "on",
    "off",
    "set_temperature",
    "set_mode",
    "set_fan_speed",
    "set_swing",
]

#: Default per-fan-speed power draw in WATTS for a 12 000 BTU split AC
#: (Pular GWH12AGB-I-R32 baseline) operating in cool/heat mode. These
#: are *estimates* — the Gree LAN protocol does not expose real-time
#: wattage. They can be overridden per-device via:
#:     meta.gree.power_max_watts = 1100   # scale the whole table
#: or
#:     meta.gree.power_estimate = {"cool": {"high": 950, ...}, ...}
_POWER_DEFAULT_MAX_WATTS = 1100  # GWH12 cooling at high fan ≈ 1.0–1.1 kW
_POWER_DEFAULT_FAN_RATIO = {
    "auto":        0.65,
    "low":         0.36,
    "medium_low":  0.50,
    "medium":      0.68,
    "medium_high": 0.82,
    "high":        1.00,
}
_POWER_MODE_RATIO = {
    "cool": 1.00,
    "heat": 1.00,
    "dry":  0.18,
    "fan":  0.05,   # fan-only — only the indoor blower runs
    "auto": 0.85,   # somewhere between cool and idle on average
}
_POWER_TURBO_BOOST = 1.30
_POWER_QUIET_DAMP = 0.70


def _enum_maps() -> dict[str, Any]:
    """Build enum ↔ string maps lazily so importing this file does not
    require greeclimate to be installed (e.g. unit tests use stubs)."""
    try:
        from greeclimate.device import (  # type: ignore
            FanSpeed,
            HorizontalSwing,
            Mode,
            VerticalSwing,
        )
    except ImportError as exc:  # pragma: no cover - import-time guard
        raise DriverError(
            "greeclimate not installed — run pip install greeclimate"
        ) from exc

    mode_to_gree = {
        "auto": Mode.Auto,
        "cool": Mode.Cool,
        "dry": Mode.Dry,
        "fan": Mode.Fan,
        "heat": Mode.Heat,
    }
    fan_to_gree = {
        "auto": FanSpeed.Auto,
        "low": FanSpeed.Low,
        "medium_low": FanSpeed.MediumLow,
        "medium": FanSpeed.Medium,
        "medium_high": FanSpeed.MediumHigh,
        "high": FanSpeed.High,
    }
    vswing_to_gree = {
        "off": VerticalSwing.Default,
        "full": VerticalSwing.FullSwing,
        "fixed_top": VerticalSwing.FixedUpper,
        "fixed_middle_top": VerticalSwing.FixedUpperMiddle,
        "fixed_middle": VerticalSwing.FixedMiddle,
        "fixed_middle_bottom": VerticalSwing.FixedLowerMiddle,
        "fixed_bottom": VerticalSwing.FixedLower,
        "swing_bottom": VerticalSwing.SwingLower,
        "swing_middle": VerticalSwing.SwingMiddle,
        "swing_top": VerticalSwing.SwingUpper,
    }
    hswing_to_gree = {
        "off": HorizontalSwing.Default,
        "full": HorizontalSwing.FullSwing,
        "left": HorizontalSwing.Left,
        "left_center": HorizontalSwing.LeftCenter,
        "center": HorizontalSwing.Center,
        "right_center": HorizontalSwing.RightCenter,
        "right": HorizontalSwing.Right,
    }

    return {
        "mode_to_gree": mode_to_gree,
        "mode_to_logical": {v: k for k, v in mode_to_gree.items()},
        "fan_to_gree": fan_to_gree,
        "fan_to_logical": {v: k for k, v in fan_to_gree.items()},
        "vswing_to_gree": vswing_to_gree,
        "vswing_to_logical": {v: k for k, v in vswing_to_gree.items()},
        "hswing_to_gree": hswing_to_gree,
        "hswing_to_logical": {v: k for k, v in hswing_to_gree.items()},
    }


def _clamp_temp(value: Any) -> int:
    """Coerce ``value`` into the AC's allowed °C range."""
    try:
        v = int(value)
    except (TypeError, ValueError) as exc:
        raise DriverError(f"Invalid target_temp: {value!r}") from exc
    if v < TEMP_MIN:
        return TEMP_MIN
    if v > TEMP_MAX:
        return TEMP_MAX
    return v


# ── Driver ─────────────────────────────────────────────────────────────────


class GreeDriver(DeviceDriver):
    protocol = "gree"

    def __init__(self, device_id: str, meta: dict[str, Any]) -> None:
        super().__init__(device_id, meta)
        cfg = (meta or {}).get("gree") or {}
        self._ip: str = str(cfg.get("ip") or "")
        self._mac: str = str(cfg.get("mac") or "")
        self._name: str = str(cfg.get("name") or device_id)
        self._port: int = int(cfg.get("port") or DEFAULT_PORT)
        self._key: str | None = cfg.get("key") or None
        self._device: Any = None  # greeclimate.device.Device
        self._lock = asyncio.Lock()
        self._last_state: dict[str, Any] | None = None
        # Populated lazily so the test stub can run without greeclimate.
        self._enums: dict[str, Any] | None = None
        # Cached power estimate, drained one-shot by consume_metering().
        self._last_metering: dict[str, float] | None = None
        # Per-device override (optional). Falls back to module defaults.
        self._power_max_watts: float = float(
            cfg.get("power_max_watts") or _POWER_DEFAULT_MAX_WATTS
        )

    # ── Helpers ─────────────────────────────────────────────────────────

    def _ensure_enums(self) -> dict[str, Any]:
        if self._enums is None:
            self._enums = _enum_maps()
        return self._enums

    def _build_device(self) -> Any:
        try:
            from greeclimate.device import Device, DeviceInfo  # type: ignore
        except ImportError as exc:
            raise DriverError(
                "greeclimate not installed — run pip install greeclimate"
            ) from exc
        if not self._ip or not self._mac:
            raise DriverError(
                f"Gree device {self.device_id}: missing ip/mac in meta.gree"
            )
        info = DeviceInfo(self._ip, self._port, self._mac, self._name)
        return Device(info)

    def _estimate_watts(self, state: dict[str, Any]) -> float:
        """Rough power-draw estimate for the current logical state.

        The Gree LAN protocol exposes no real wattage — this is a model
        based on mode + fan speed + boolean flags, calibrated to a
        12 000 BTU split AC. Override per-device with
        ``meta.gree.power_max_watts``.
        """
        if not state.get("on"):
            return 0.0
        mode = str(state.get("mode") or "auto").lower()
        fan = str(state.get("fan_speed") or "auto").lower()
        mode_ratio = _POWER_MODE_RATIO.get(mode, 0.85)
        fan_ratio = _POWER_DEFAULT_FAN_RATIO.get(fan, 0.65)
        watts = self._power_max_watts * mode_ratio * fan_ratio
        if state.get("turbo"):
            watts *= _POWER_TURBO_BOOST
        if state.get("quiet"):
            watts *= _POWER_QUIET_DAMP
        # Indoor blower runs even in dry/fan modes — never report less
        # than ~15 W when the unit is powered on.
        return round(max(15.0, watts), 1)

    def consume_metering(self) -> dict[str, float] | None:
        """One-shot read of the latest power-draw estimate.

        See ``DeviceDriver.consume_metering`` — the watcher loop calls
        this after each state change and forwards the snapshot to the
        EventBus as ``device.power_reading`` so ``energy-monitor`` can
        record kWh accumulation.
        """
        m = self._last_metering
        self._last_metering = None
        return m

    def _to_logical(self, dev: Any) -> dict[str, Any]:
        """Translate the greeclimate Device object into our logical state."""
        e = self._ensure_enums()
        out: dict[str, Any] = {}
        # Power
        out["on"] = bool(getattr(dev, "power", False))
        # Mode
        mode = getattr(dev, "mode", None)
        if mode is not None:
            out["mode"] = e["mode_to_logical"].get(mode, "auto")
        # Temperatures
        tt = getattr(dev, "target_temperature", None)
        if tt is not None:
            try:
                out["target_temp"] = int(tt)
            except (TypeError, ValueError):
                pass
        ct = getattr(dev, "current_temperature", None)
        if ct is not None:
            try:
                out["current_temp"] = int(ct)
            except (TypeError, ValueError):
                pass
        # Fan
        fan = getattr(dev, "fan_speed", None)
        if fan is not None:
            out["fan_speed"] = e["fan_to_logical"].get(fan, "auto")
        # Swing
        vs = getattr(dev, "vertical_swing", None)
        if vs is not None:
            out["swing_v"] = e["vswing_to_logical"].get(vs, "off")
        hs = getattr(dev, "horizontal_swing", None)
        if hs is not None:
            out["swing_h"] = e["hswing_to_logical"].get(hs, "off")
        # Boolean toggles
        for logical_key, attr in (
            ("sleep", "sleep"),
            ("turbo", "turbo"),
            ("light", "light"),
            ("eco", "steady_heat"),  # greeclimate calls eco "steady_heat"
            ("health", "anion"),     # health/ionizer
            ("quiet", "quiet"),
        ):
            val = getattr(dev, attr, None)
            if val is not None:
                out[logical_key] = bool(val)
        # Side effect: refresh the cached power estimate so the watcher
        # picks it up via consume_metering() on the next iteration.
        self._last_metering = {"watts": self._estimate_watts(out)}
        return out

    def _apply_logical(self, state: dict[str, Any]) -> None:
        """Translate logical state keys onto the greeclimate Device object.

        Caller must hold ``self._lock`` and ensure ``self._device`` exists.
        """
        e = self._ensure_enums()
        dev = self._device
        for key, value in state.items():
            if key == "on":
                dev.power = bool(value)
            elif key == "mode":
                m = e["mode_to_gree"].get(str(value).lower())
                if m is None:
                    raise DriverError(f"Unknown mode: {value!r}")
                dev.mode = m
            elif key == "target_temp":
                dev.target_temperature = _clamp_temp(value)
            elif key == "fan_speed":
                f = e["fan_to_gree"].get(str(value).lower())
                if f is None:
                    raise DriverError(f"Unknown fan_speed: {value!r}")
                dev.fan_speed = f
            elif key == "swing_v":
                s = e["vswing_to_gree"].get(str(value).lower())
                if s is None:
                    raise DriverError(f"Unknown swing_v: {value!r}")
                dev.vertical_swing = s
            elif key == "swing_h":
                s = e["hswing_to_gree"].get(str(value).lower())
                if s is None:
                    raise DriverError(f"Unknown swing_h: {value!r}")
                dev.horizontal_swing = s
            elif key == "sleep":
                dev.sleep = bool(value)
            elif key == "turbo":
                dev.turbo = bool(value)
            elif key == "light":
                dev.light = bool(value)
            elif key == "eco":
                dev.steady_heat = bool(value)
            elif key == "health":
                dev.anion = bool(value)
            elif key == "quiet":
                dev.quiet = bool(value)
            else:
                logger.debug("gree %s: ignoring unknown state key %r",
                             self.device_id, key)

    # ── DeviceDriver interface ──────────────────────────────────────────

    async def connect(self) -> dict[str, Any]:
        async with self._lock:
            if self._device is None:
                self._device = self._build_device()
            # greeclimate v2's ``bind()`` insists on receiving (key, cipher)
            # *together* — passing key alone raises "cipher must be provided
            # when key is provided". Doing a fresh, no-arg bind on every
            # connect is the simplest and most robust approach: greeclimate
            # auto-detects V1/V2 cipher, costs ~100ms once on startup, and
            # avoids us having to track cipher version in meta.
            try:
                await self._device.bind()
            except Exception as exc:
                self._device = None
                raise DriverError(
                    f"Gree bind failed for {self.device_id}: {exc}"
                ) from exc
            # Cache the negotiated key for diagnostics — not used by reconnect
            # logic anymore but handy in API responses.
            new_key = getattr(self._device, "device_key", None)
            if new_key:
                self._key = new_key
                gree_meta = self.meta.setdefault("gree", {})
                gree_meta["key"] = new_key
            try:
                await self._device.update_state()
            except Exception as exc:
                raise DriverError(
                    f"Gree update_state failed for {self.device_id}: {exc}"
                ) from exc
            state = self._to_logical(self._device)
        self._last_state = dict(state)
        return state

    async def disconnect(self) -> None:
        async with self._lock:
            self._device = None

    async def set_state(self, state: dict[str, Any]) -> None:
        if not state:
            return
        if self._device is None:
            await self.connect()
        async with self._lock:
            try:
                self._apply_logical(state)
                await self._device.push_state_update()
            except DriverError:
                raise
            except Exception as exc:
                raise DriverError(
                    f"Gree set_state failed for {self.device_id}: {exc}"
                ) from exc

    async def get_state(self) -> dict[str, Any]:
        if self._device is None:
            return await self.connect()
        async with self._lock:
            try:
                await self._device.update_state()
            except Exception as exc:
                raise DriverError(
                    f"Gree get_state failed for {self.device_id}: {exc}"
                ) from exc
            state = self._to_logical(self._device)
        self._last_state = dict(state)
        return state

    async def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        # Gree devices do not push events on their own — poll periodically
        # and yield only when the state actually changes. The watcher loop
        # already coalesces redundant events at its end, but doing the diff
        # here avoids burning EventBus traffic on idle ACs.
        if self._device is None:
            await self.connect()
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            async with self._lock:
                try:
                    await self._device.update_state()
                except Exception as exc:
                    raise DriverError(
                        f"Gree poll failed for {self.device_id}: {exc}"
                    ) from exc
                state = self._to_logical(self._device)
            if state != self._last_state:
                self._last_state = dict(state)
                yield state
