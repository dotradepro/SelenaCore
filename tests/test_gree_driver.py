"""Unit tests for the Gree A/C driver — pure mappers, no network."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


# ── Stub greeclimate before importing the driver ───────────────────────────
#
# greeclimate is an optional runtime dependency. The unit tests target the
# pure-Python mapping logic, so we install a lightweight stub module under
# ``greeclimate.device`` exposing the enums and ``Device`` / ``DeviceInfo``
# classes the driver imports. Each enum value is just a unique sentinel so
# the bidirectional maps round-trip cleanly.

if "greeclimate" not in sys.modules:
    pkg = types.ModuleType("greeclimate")
    device_mod = types.ModuleType("greeclimate.device")
    discovery_mod = types.ModuleType("greeclimate.discovery")

    class _Enum:
        """Minimal stand-in for an enum.IntEnum-like class."""

        def __init__(self, name: str) -> None:
            self.name = name

        def __repr__(self) -> str:  # pragma: no cover - debug aid
            return f"<{self.name}>"

        def __hash__(self) -> int:
            return hash(self.name)

        def __eq__(self, other: object) -> bool:
            return isinstance(other, _Enum) and self.name == other.name

    class Mode:
        Auto = _Enum("Auto")
        Cool = _Enum("Cool")
        Dry = _Enum("Dry")
        Fan = _Enum("Fan")
        Heat = _Enum("Heat")

    class FanSpeed:
        Auto = _Enum("FAuto")
        Low = _Enum("FLow")
        MediumLow = _Enum("FMedLow")
        Medium = _Enum("FMed")
        MediumHigh = _Enum("FMedHigh")
        High = _Enum("FHigh")

    class VerticalSwing:
        Default = _Enum("VDefault")
        FullSwing = _Enum("VFull")
        FixedUpper = _Enum("VFixedUpper")
        FixedUpperMiddle = _Enum("VFixedUpperMid")
        FixedMiddle = _Enum("VFixedMid")
        FixedLowerMiddle = _Enum("VFixedLowerMid")
        FixedLower = _Enum("VFixedLower")
        SwingLower = _Enum("VSwingLower")
        SwingMiddle = _Enum("VSwingMid")
        SwingUpper = _Enum("VSwingUpper")

    class HorizontalSwing:
        Default = _Enum("HDefault")
        FullSwing = _Enum("HFull")
        Left = _Enum("HLeft")
        LeftCenter = _Enum("HLeftCtr")
        Center = _Enum("HCenter")
        RightCenter = _Enum("HRightCtr")
        Right = _Enum("HRight")

    class DeviceInfo:
        def __init__(self, ip, port, mac, name):
            self.ip = ip
            self.port = port
            self.mac = mac
            self.name = name

    class Device:
        def __init__(self, info):
            self.info = info

    device_mod.Mode = Mode
    device_mod.FanSpeed = FanSpeed
    device_mod.VerticalSwing = VerticalSwing
    device_mod.HorizontalSwing = HorizontalSwing
    device_mod.DeviceInfo = DeviceInfo
    device_mod.Device = Device

    sys.modules["greeclimate"] = pkg
    sys.modules["greeclimate.device"] = device_mod
    sys.modules["greeclimate.discovery"] = discovery_mod


from system_modules.device_control.drivers.base import DriverError  # noqa: E402
from system_modules.device_control.drivers.gree import (  # noqa: E402
    AC_CAPABILITIES,
    GreeDriver,
    TEMP_MAX,
    TEMP_MIN,
    _clamp_temp,
    _enum_maps,
)


def test_capabilities_constant():
    assert "on" in AC_CAPABILITIES
    assert "set_temperature" in AC_CAPABILITIES
    assert "set_mode" in AC_CAPABILITIES
    assert "set_fan_speed" in AC_CAPABILITIES


@pytest.mark.parametrize(
    "raw,expected",
    [(15, 16), (16, 16), (22, 22), (30, 30), (31, 30), (99, 30), (5, 16)],
)
def test_clamp_temp_bounds(raw, expected):
    assert _clamp_temp(raw) == expected


def test_clamp_temp_invalid():
    with pytest.raises(DriverError):
        _clamp_temp("not-a-number")


def test_enum_maps_round_trip():
    e = _enum_maps()
    for logical, gree in e["mode_to_gree"].items():
        assert e["mode_to_logical"][gree] == logical
    for logical, gree in e["fan_to_gree"].items():
        assert e["fan_to_logical"][gree] == logical
    for logical, gree in e["vswing_to_gree"].items():
        assert e["vswing_to_logical"][gree] == logical
    for logical, gree in e["hswing_to_gree"].items():
        assert e["hswing_to_logical"][gree] == logical


def _make_driver(meta_extra: dict | None = None) -> GreeDriver:
    meta = {"gree": {"ip": "192.168.1.50", "mac": "aa:bb:cc:dd:ee:ff", "name": "AC"}}
    if meta_extra:
        meta["gree"].update(meta_extra)
    return GreeDriver("dev-1", meta)


def test_to_logical_full_state():
    drv = _make_driver()
    e = _enum_maps()
    fake = MagicMock()
    fake.power = True
    fake.mode = e["mode_to_gree"]["cool"]
    fake.target_temperature = 22
    fake.current_temperature = 25
    fake.fan_speed = e["fan_to_gree"]["medium"]
    fake.vertical_swing = e["vswing_to_gree"]["full"]
    fake.horizontal_swing = e["hswing_to_gree"]["center"]
    fake.sleep = False
    fake.turbo = True
    fake.light = True
    fake.steady_heat = False
    fake.anion = True
    fake.quiet = False

    state = drv._to_logical(fake)
    assert state["on"] is True
    assert state["mode"] == "cool"
    assert state["target_temp"] == 22
    assert state["current_temp"] == 25
    assert state["fan_speed"] == "medium"
    assert state["swing_v"] == "full"
    assert state["swing_h"] == "center"
    assert state["turbo"] is True
    assert state["health"] is True


def test_apply_logical_clamps_temperature():
    drv = _make_driver()
    drv._device = MagicMock()
    drv._apply_logical({"target_temp": 99})
    assert drv._device.target_temperature == TEMP_MAX
    drv._apply_logical({"target_temp": 5})
    assert drv._device.target_temperature == TEMP_MIN


def test_apply_logical_unknown_mode_raises():
    drv = _make_driver()
    drv._device = MagicMock()
    with pytest.raises(DriverError):
        drv._apply_logical({"mode": "warp_speed"})


def test_apply_logical_translates_eco_and_health():
    drv = _make_driver()
    drv._device = MagicMock()
    drv._apply_logical({"eco": True, "health": True, "quiet": True, "light": False})
    assert drv._device.steady_heat is True
    assert drv._device.anion is True
    assert drv._device.quiet is True
    assert drv._device.light is False


def test_init_reads_meta_correctly():
    drv = _make_driver({"port": 7000, "key": "abc123"})
    assert drv._ip == "192.168.1.50"
    assert drv._mac == "aa:bb:cc:dd:ee:ff"
    assert drv._port == 7000
    assert drv._key == "abc123"
