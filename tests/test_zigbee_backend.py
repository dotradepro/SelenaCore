"""Zigbee scanner backend coordination — z2m vs zigpy.

The native zigpy scanner and the zigbee2mqtt MQTT subscription must
NEVER both try to drive /dev/ttyUSB0. ``is_dongle_available()`` is the
gate that prevents the scanner from running when z2m is configured.
"""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture
def reload_scanner(monkeypatch):
    """Re-import zigbee_scanner with patched env so module-level constants
    pick up the override."""
    def _reload(env: dict):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        import system_modules.network_scanner.zigbee_scanner as zs
        importlib.reload(zs)
        return zs
    return _reload


def test_z2m_backend_disables_dongle_access(reload_scanner, tmp_path):
    """When ZIGBEE_BACKEND=z2m, is_dongle_available() must return False
    even if the device file exists — protocol_bridge owns the dongle."""
    fake_dongle = tmp_path / "ttyUSB0"
    fake_dongle.touch()
    zs = reload_scanner({
        "ZIGBEE_BACKEND": "z2m",
        "ZIGBEE_SERIAL_PORT": str(fake_dongle),
    })
    assert zs.is_dongle_available() is False


def test_none_backend_disables_dongle_access(reload_scanner, tmp_path):
    fake_dongle = tmp_path / "ttyUSB0"
    fake_dongle.touch()
    zs = reload_scanner({
        "ZIGBEE_BACKEND": "none",
        "ZIGBEE_SERIAL_PORT": str(fake_dongle),
    })
    assert zs.is_dongle_available() is False


def test_zigpy_backend_uses_real_check(reload_scanner, tmp_path):
    """zigpy backend (default) honours filesystem existence."""
    missing = tmp_path / "nope"
    zs = reload_scanner({
        "ZIGBEE_BACKEND": "zigpy",
        "ZIGBEE_SERIAL_PORT": str(missing),
    })
    assert zs.is_dongle_available() is False

    fake_dongle = tmp_path / "ttyUSB0"
    fake_dongle.touch()
    zs = reload_scanner({
        "ZIGBEE_BACKEND": "zigpy",
        "ZIGBEE_SERIAL_PORT": str(fake_dongle),
    })
    assert zs.is_dongle_available() is True


@pytest.mark.asyncio
async def test_z2m_backend_scan_returns_empty(reload_scanner, tmp_path):
    """scan_zigbee_network() short-circuits to [] under z2m backend."""
    fake_dongle = tmp_path / "ttyUSB0"
    fake_dongle.touch()
    zs = reload_scanner({
        "ZIGBEE_BACKEND": "z2m",
        "ZIGBEE_SERIAL_PORT": str(fake_dongle),
    })
    result = await zs.scan_zigbee_network(timeout=1.0)
    assert result == []


@pytest.mark.asyncio
async def test_z2m_backend_permit_join_returns_false(reload_scanner, tmp_path):
    fake_dongle = tmp_path / "ttyUSB0"
    fake_dongle.touch()
    zs = reload_scanner({
        "ZIGBEE_BACKEND": "z2m",
        "ZIGBEE_SERIAL_PORT": str(fake_dongle),
    })
    assert await zs.permit_join(duration_sec=10) is False


def test_unknown_backend_warns_and_falls_back(reload_scanner, caplog, tmp_path):
    """Typos like 'zigbee2mqtt' or 'ZIGPY-znp' must log a WARNING and not
    silently disable Zigbee — Zigbee just becomes inaccessible, and the
    user needs to know why."""
    import logging
    fake_dongle = tmp_path / "ttyUSB0"
    fake_dongle.touch()
    with caplog.at_level(logging.WARNING):
        zs = reload_scanner({
            "ZIGBEE_BACKEND": "zigbee2mqtt",  # common typo for "z2m"
            "ZIGBEE_SERIAL_PORT": str(fake_dongle),
        })
    assert zs.ZIGBEE_BACKEND == "none"
    assert zs.is_dongle_available() is False
    # Verify the warning was actually emitted with the offending value.
    matched = [
        r for r in caplog.records
        if "unknown ZIGBEE_BACKEND" in r.message and "zigbee2mqtt" in r.message
    ]
    assert matched, "expected warning about unknown ZIGBEE_BACKEND value"


def test_empty_backend_string_falls_back_to_none(reload_scanner, tmp_path):
    """Empty string env var must not crash; fall through to 'none'."""
    fake_dongle = tmp_path / "ttyUSB0"
    fake_dongle.touch()
    zs = reload_scanner({
        "ZIGBEE_BACKEND": "",
        "ZIGBEE_SERIAL_PORT": str(fake_dongle),
    })
    assert zs.ZIGBEE_BACKEND == "none"
    assert zs.is_dongle_available() is False
