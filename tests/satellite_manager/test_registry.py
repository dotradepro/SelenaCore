"""Unit tests for satellite_manager.satellite_registry against an in-memory SQLite DB."""
from __future__ import annotations

import sys

import pytest

if sys.version_info < (3, 10):
    pytest.skip(
        "core.registry.models needs Python 3.10+ to evaluate Mapped[...] annotations",
        allow_module_level=True,
    )

import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from core.registry.models import Base  # noqa: E402
from system_modules.satellite_manager.satellite_registry import (  # noqa: E402
    MODULE_ID,
    SatelliteRegistry,
    device_id_for_mac,
)


@pytest_asyncio.fixture
async def sf():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def registry(sf):
    return SatelliteRegistry(sf)


def test_device_id_for_mac_strips_separators():
    assert device_id_for_mac("AA:BB:CC:11:22:33") == "sat_aabbcc112233"
    assert device_id_for_mac("aa-bb-cc-11-22-33") == "sat_aabbcc112233"
    assert device_id_for_mac("aabbcc112233") == "sat_aabbcc112233"


async def test_register_creates_row(registry):
    result = await registry.register(
        mac="AA:BB:CC:11:22:33",
        firmware="1.0.0",
        hardware="esp32_audio_kit",
        capabilities=["mic_stereo", "speaker_stereo"],
        ip="192.168.1.50",
    )
    assert result["device_id"] == "sat_aabbcc112233"
    assert result["location"] is None

    row = await registry.get("sat_aabbcc112233")
    assert row is not None
    assert row["meta"]["mac"] == "AA:BB:CC:11:22:33"
    assert row["meta"]["firmware"] == "1.0.0"
    assert row["meta"]["ip"] == "192.168.1.50"
    assert row["state"]["online"] is True
    assert row["state"]["volume"] == 75
    assert row["capabilities"] == ["mic_stereo", "speaker_stereo"]


async def test_register_is_idempotent(registry):
    await registry.register(
        mac="AA:BB:CC:11:22:33", firmware="1.0.0", hardware="esp32",
        capabilities=["mic"], ip="192.168.1.50",
    )
    await registry.update("sat_aabbcc112233", location="kitchen", name="Kitchen sat")

    # Re-registration (e.g. ESP32 rebooted with new IP) must not wipe location/name
    result = await registry.register(
        mac="AA:BB:CC:11:22:33", firmware="1.1.0", hardware="esp32",
        capabilities=["mic"], ip="192.168.1.51",
    )
    assert result["location"] == "kitchen"

    row = await registry.get("sat_aabbcc112233")
    assert row["location"] == "kitchen"
    assert row["name"] == "Kitchen sat"
    assert row["meta"]["ip"] == "192.168.1.51"
    assert row["meta"]["firmware"] == "1.1.0"
    assert row["state"]["online"] is True


async def test_list_all_filters_by_module_id(registry, sf):
    await registry.register(
        mac="AA:BB:CC:11:22:33", firmware="1.0", hardware="esp32",
        capabilities=[], ip=None,
    )
    # Insert a foreign-module device — must be excluded from list_all()
    from core.registry.models import Device
    async with sf() as session:
        other = Device(
            device_id="not_a_sat",
            name="Kitchen light",
            type="actuator",
            protocol="zigbee",
            module_id="device-control",
            entity_type="light",
        )
        session.add(other)
        await session.commit()

    rows = await registry.list_all()
    assert len(rows) == 1
    assert rows[0]["device_id"] == "sat_aabbcc112233"


async def test_get_rejects_wrong_module_id(registry, sf):
    from core.registry.models import Device
    async with sf() as session:
        other = Device(
            device_id="sat_foreign",
            name="Not ours",
            type="speaker",
            protocol="zigbee",
            module_id="device-control",
            entity_type="satellite_speaker",
        )
        session.add(other)
        await session.commit()

    # Get must refuse to return rows owned by another module, even if the id looks satellite-ish
    assert await registry.get("sat_foreign") is None


async def test_update_name_location_volume(registry):
    await registry.register(
        mac="AA:BB:CC:11:22:33", firmware="1.0", hardware="esp32",
        capabilities=[], ip=None,
    )
    ok = await registry.update(
        "sat_aabbcc112233",
        name="Kitchen Sat",
        location="kitchen",
        volume=60,
        muted=True,
    )
    assert ok is True

    row = await registry.get("sat_aabbcc112233")
    assert row["name"] == "Kitchen Sat"
    assert row["location"] == "kitchen"
    assert row["state"]["volume"] == 60
    assert row["state"]["muted"] is True


async def test_update_returns_false_for_unknown(registry):
    assert await registry.update("sat_does_not_exist", name="x") is False


async def test_set_online_toggles_state(registry):
    await registry.register(
        mac="AA:BB:CC:11:22:33", firmware="1.0", hardware="esp32",
        capabilities=[], ip=None,
    )
    await registry.set_online("sat_aabbcc112233", False)
    row = await registry.get("sat_aabbcc112233")
    assert row["state"]["online"] is False

    await registry.set_online("sat_aabbcc112233", True)
    row = await registry.get("sat_aabbcc112233")
    assert row["state"]["online"] is True


async def test_update_state_partial_merge(registry):
    await registry.register(
        mac="AA:BB:CC:11:22:33", firmware="1.0", hardware="esp32",
        capabilities=[], ip=None,
    )
    await registry.update_state("sat_aabbcc112233", {"rssi": -55, "volume": 40})
    row = await registry.get("sat_aabbcc112233")
    assert row["state"]["rssi"] == -55
    assert row["state"]["volume"] == 40
    assert row["state"]["online"] is True  # preserved
    assert row["state"]["muted"] is False  # preserved


async def test_delete(registry):
    await registry.register(
        mac="AA:BB:CC:11:22:33", firmware="1.0", hardware="esp32",
        capabilities=[], ip=None,
    )
    assert await registry.delete("sat_aabbcc112233") is True
    assert await registry.get("sat_aabbcc112233") is None
    assert await registry.delete("sat_aabbcc112233") is False


async def test_delete_refuses_foreign_module(registry, sf):
    from core.registry.models import Device
    async with sf() as session:
        other = Device(
            device_id="sat_foreign",
            name="Not ours",
            type="speaker",
            protocol="zigbee",
            module_id="device-control",
        )
        session.add(other)
        await session.commit()

    assert await registry.delete("sat_foreign") is False
    # Row must still exist
    async with sf() as session:
        assert await session.get(Device, "sat_foreign") is not None


async def test_stored_module_id_and_entity_type(registry, sf):
    await registry.register(
        mac="AA:BB:CC:11:22:33", firmware="1.0", hardware="esp32",
        capabilities=[], ip=None,
    )
    from core.registry.models import Device
    async with sf() as session:
        device = await session.get(Device, "sat_aabbcc112233")
        assert device.module_id == MODULE_ID
        assert device.entity_type == "satellite_speaker"
        assert device.type == "speaker"
        assert device.protocol == "selena_satellite"
