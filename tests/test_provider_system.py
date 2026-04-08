"""Unit tests for the device-control provider system + Tuya classifier.

These tests don't touch SQLite or pip — they exercise the pure-Python
catalog + classifier logic to make sure refactors don't silently break
auto-routing or built-in detection.
"""
from __future__ import annotations

import sys
import types

# Stub greeclimate before importing the device_control package, so the
# catalog import doesn't crash on a missing dep in CI.
if "greeclimate" not in sys.modules:
    pkg = types.ModuleType("greeclimate")
    device_mod = types.ModuleType("greeclimate.device")
    discovery_mod = types.ModuleType("greeclimate.discovery")
    class _E:
        def __init__(self, name=""): self.name = name
    class Mode: Auto=_E(); Cool=_E(); Dry=_E(); Fan=_E(); Heat=_E()
    class FanSpeed: Auto=_E(); Low=_E(); MediumLow=_E(); Medium=_E(); MediumHigh=_E(); High=_E()
    class VerticalSwing:
        Default=_E(); FullSwing=_E(); FixedUpper=_E(); FixedUpperMiddle=_E()
        FixedMiddle=_E(); FixedLowerMiddle=_E(); FixedLower=_E()
        SwingLower=_E(); SwingMiddle=_E(); SwingUpper=_E()
    class HorizontalSwing:
        Default=_E(); FullSwing=_E(); Left=_E(); LeftCenter=_E()
        Center=_E(); RightCenter=_E(); Right=_E()
    class DeviceInfo:
        def __init__(self, *a, **k): pass
    class Device:
        def __init__(self, *a, **k): pass
    device_mod.Mode = Mode
    device_mod.FanSpeed = FanSpeed
    device_mod.VerticalSwing = VerticalSwing
    device_mod.HorizontalSwing = HorizontalSwing
    device_mod.DeviceInfo = DeviceInfo
    device_mod.Device = Device
    sys.modules["greeclimate"] = pkg
    sys.modules["greeclimate.device"] = device_mod
    sys.modules["greeclimate.discovery"] = discovery_mod


from system_modules.device_control.providers.catalog import (  # noqa: E402
    PROVIDERS,
    builtin_provider_ids,
    get_provider,
)
from system_modules.device_control.routes import _classify_tuya_entity_type  # noqa: E402


# ── Catalog ────────────────────────────────────────────────────────────────


def test_catalog_has_core_providers():
    assert "tuya_local" in PROVIDERS
    assert "tuya_cloud" in PROVIDERS
    assert "gree" in PROVIDERS
    assert "mqtt" in PROVIDERS


def test_builtin_provider_ids_marks_tuya_and_gree():
    builtins = set(builtin_provider_ids())
    assert "tuya_local" in builtins
    assert "tuya_cloud" in builtins
    assert "gree" in builtins
    assert "mqtt" in builtins
    # Opt-in extras should NOT be flagged builtin
    assert "philips_hue" not in builtins
    assert "esphome" not in builtins
    assert "zigbee2mqtt" not in builtins


def test_get_provider_returns_spec_or_none():
    spec = get_provider("gree")
    assert spec is not None
    assert spec["package"] == "greeclimate"
    assert "air_conditioner" in spec["entity_types"]
    assert get_provider("nonexistent") is None


def test_every_provider_has_required_fields():
    required = {"id", "name", "driver_module", "driver_class", "entity_types"}
    for pid, spec in PROVIDERS.items():
        missing = required - set(spec.keys())
        assert not missing, f"{pid} missing fields: {missing}"
        # entity_types must be a non-empty list
        assert isinstance(spec["entity_types"], list)
        assert len(spec["entity_types"]) > 0


# ── Tuya entity_type classifier ────────────────────────────────────────────


def test_classifier_detects_lighting_category():
    cd = {"category": "dj", "name": "Bulb", "product_name": "RGB Light"}
    et, caps = _classify_tuya_entity_type(cd)
    assert et == "light"
    assert "on" in caps and "off" in caps


def test_classifier_detects_outlet_category():
    cd = {"category": "cz", "name": "Smart Plug", "product_name": "Outlet"}
    et, caps = _classify_tuya_entity_type(cd)
    assert et == "outlet"


def test_classifier_uses_product_name_keyword_for_light():
    cd = {"category": "", "name": "Kitchen lamp", "product_name": "led lamp"}
    et, _ = _classify_tuya_entity_type(cd)
    assert et == "light"


def test_classifier_uses_ukrainian_keyword_for_light():
    cd = {"category": "", "name": "Кухня", "product_name": "Розумна лампа"}
    et, _ = _classify_tuya_entity_type(cd)
    assert et == "light"


def test_classifier_falls_back_to_switch():
    cd = {"category": "abc", "name": "Mystery", "product_name": ""}
    et, _ = _classify_tuya_entity_type(cd)
    assert et == "switch"


def test_classifier_brightness_capability_when_present_in_status():
    cd = {
        "category": "dj",
        "name": "Bulb",
        "product_name": "Smart bulb",
        "status": {"switch_led": True, "bright_value": 800, "temp_value": 500},
    }
    _, caps = _classify_tuya_entity_type(cd)
    assert "brightness" in caps
    assert "colour_temp" in caps
