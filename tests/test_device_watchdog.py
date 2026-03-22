"""
tests/test_device_watchdog.py — pytest тесты для модуля device_watchdog [#70]
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from system_modules.device_watchdog.watchdog import DeviceWatchdog, DeviceStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_watchdog(
    devices=None,
    config=None,
    publish=None,
    update=None,
):
    devices = devices or []
    publish = publish or AsyncMock()
    update = update or AsyncMock()
    get_devices = AsyncMock(return_value=devices)
    return DeviceWatchdog(
        publish_callback=publish,
        get_devices_callback=get_devices,
        update_device_callback=update,
        config=config or {},
    ), publish, update, get_devices


def wifi_device(device_id="dev1", ip="192.168.1.10"):
    return {
        "device_id": device_id,
        "name": "Test WiFi",
        "protocol": "wifi",
        "meta": {"ip_address": ip},
    }


def mqtt_device(device_id="dev2", last_seen_offset_sec=-50):
    ts = (datetime.now(tz=timezone.utc) + timedelta(seconds=last_seen_offset_sec)).isoformat()
    return {
        "device_id": device_id,
        "name": "MQTT Device",
        "protocol": "mqtt",
        "meta": {"mqtt_last_seen": ts},
    }


def zigbee_device(device_id="dev3", last_seen_offset_sec=-100):
    ts = (datetime.now(tz=timezone.utc) + timedelta(seconds=last_seen_offset_sec)).isoformat()
    return {
        "device_id": device_id,
        "name": "Zigbee Sensor",
        "protocol": "zigbee",
        "meta": {"protocol_last_seen": ts},
    }


# ── ICMP ping tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_icmp_ping_success():
    wd, *_ = make_watchdog()
    mock_result = MagicMock()
    mock_result.is_alive = True
    with patch("system_modules.device_watchdog.watchdog.ICMPLIB_AVAILABLE", True), \
         patch("system_modules.device_watchdog.watchdog.icmplib_ping",
               new_callable=AsyncMock, return_value=mock_result):
        result = await wd._icmp_ping("192.168.1.1")
    assert result is True


@pytest.mark.asyncio
async def test_icmp_ping_failure():
    wd, *_ = make_watchdog()
    mock_result = MagicMock()
    mock_result.is_alive = False
    with patch("system_modules.device_watchdog.watchdog.ICMPLIB_AVAILABLE", True), \
         patch("system_modules.device_watchdog.watchdog.icmplib_ping",
               new_callable=AsyncMock, return_value=mock_result):
        result = await wd._icmp_ping("192.168.1.200")
    assert result is False


@pytest.mark.asyncio
async def test_icmp_ping_exception_returns_false():
    wd, *_ = make_watchdog()
    with patch("system_modules.device_watchdog.watchdog.ICMPLIB_AVAILABLE", True), \
         patch("system_modules.device_watchdog.watchdog.icmplib_ping",
               new_callable=AsyncMock, side_effect=OSError("permission denied")):
        result = await wd._icmp_ping("192.168.1.1")
    assert result is False


# ── MQTT timeout tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mqtt_within_timeout_is_online():
    wd, *_ = make_watchdog(config={"mqtt_timeout_sec": 120})
    device = mqtt_device(last_seen_offset_sec=-50)  # 50s ago
    result = await wd._ping(device)
    assert result is True


@pytest.mark.asyncio
async def test_mqtt_past_timeout_is_offline():
    wd, *_ = make_watchdog(config={"mqtt_timeout_sec": 120})
    device = mqtt_device(last_seen_offset_sec=-200)  # 200s ago > 120s
    result = await wd._ping(device)
    assert result is False


@pytest.mark.asyncio
async def test_mqtt_missing_last_seen_is_offline():
    wd, *_ = make_watchdog()
    device = {"device_id": "d", "protocol": "mqtt", "meta": {}}
    result = await wd._ping(device)
    assert result is False


# ── Zigbee timeout tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_zigbee_within_timeout_online():
    wd, *_ = make_watchdog(config={"protocol_timeout_sec": 300})
    device = zigbee_device(last_seen_offset_sec=-100)
    result = await wd._ping(device)
    assert result is True


@pytest.mark.asyncio
async def test_zigbee_past_timeout_offline():
    wd, *_ = make_watchdog(config={"protocol_timeout_sec": 300})
    device = zigbee_device(last_seen_offset_sec=-400)
    result = await wd._ping(device)
    assert result is False


@pytest.mark.asyncio
async def test_zigbee_no_last_seen_assumed_online():
    wd, *_ = make_watchdog()
    device = {"device_id": "d", "protocol": "zigbee", "meta": {}}
    result = await wd._ping(device)
    assert result is True


# ── Status change events ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_device_offline_event_published_after_threshold():
    wd, publish, update, _ = make_watchdog(config={"offline_threshold": 3})

    with patch.object(wd, "_ping", return_value=False):
        # First 2 failures — below threshold
        await wd._check_device(wifi_device())
        await wd._check_device(wifi_device())
        assert publish.call_count == 0

        # 3rd failure → threshold reached → event
        await wd._check_device(wifi_device())
        event_types = [c.args[0] for c in publish.call_args_list]
        assert "device.offline" in event_types


@pytest.mark.asyncio
async def test_device_online_event_after_recovery():
    wd, publish, update, _ = make_watchdog()
    # Pre-set device as offline
    wd._statuses["dev1"] = DeviceStatus("dev1", is_online=False)

    with patch.object(wd, "_ping", return_value=True):
        await wd._check_device(wifi_device("dev1"))

    event_types = [c.args[0] for c in publish.call_args_list]
    assert "device.online" in event_types


@pytest.mark.asyncio
async def test_no_duplicate_offline_event():
    wd, publish, update, _ = make_watchdog(config={"offline_threshold": 1})

    # Mark already offline
    wd._statuses["dev1"] = DeviceStatus("dev1", is_online=False)

    with patch.object(wd, "_ping", return_value=False):
        await wd._check_device(wifi_device("dev1"))
        await wd._check_device(wifi_device("dev1"))

    # Should NOT fire again since status didn't change
    offline_events = [c for c in publish.call_args_list if c.args[0] == "device.offline"]
    assert len(offline_events) == 0


# ── Watchdog scan summary ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watchdog_scan_event_published():
    devices = [wifi_device("d1"), wifi_device("d2"), wifi_device("d3")]
    wd, publish, update, _ = make_watchdog(devices=devices)

    with patch.object(wd, "_ping", return_value=True):
        summary = await wd._run_check()

    event_types = [c.args[0] for c in publish.call_args_list]
    assert "device.watchdog_scan" in event_types
    assert summary["checked"] == 3


@pytest.mark.asyncio
async def test_watchdog_scan_counts_correctly():
    devices = [wifi_device("d1"), wifi_device("d2")]
    wd, publish, update, _ = make_watchdog(devices=devices, config={"offline_threshold": 1})

    async def fake_ping(device):
        return device["device_id"] == "d1"

    with patch.object(wd, "_ping", side_effect=fake_ping):
        summary = await wd._run_check()

    assert summary["online"] == 1
    assert summary["offline"] == 1


# ── Protocol heartbeat ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_heartbeat_recovers_offline_device():
    wd, publish, update, _ = make_watchdog()
    wd._statuses["dev5"] = DeviceStatus("dev5", is_online=False)

    await wd.on_protocol_heartbeat({"device_id": "dev5", "timestamp": "now"})

    event_types = [c.args[0] for c in publish.call_args_list]
    assert "device.online" in event_types


@pytest.mark.asyncio
async def test_heartbeat_no_device_id_ignored():
    wd, publish, *_ = make_watchdog()
    await wd.on_protocol_heartbeat({"timestamp": "now"})
    publish.assert_not_called()
