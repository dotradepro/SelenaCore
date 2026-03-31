"""
benchmarks/bench_registry.py — DeviceRegistry performance benchmarks

Tests:
  - create throughput
  - get_all with N devices
  - get by ID lookup
  - update_state throughput
  - delete throughput
  - state history trimming
"""
from __future__ import annotations

import time

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from core.registry.service import DeviceRegistry


class TestRegistryCreate:
    """Benchmark device creation."""

    @pytest.mark.asyncio
    async def test_create_100(self, db_session: AsyncSession) -> None:
        registry = DeviceRegistry(db_session)
        count = 100

        start = time.perf_counter()
        for i in range(count):
            await registry.create(
                name=f"Bench Device {i}",
                type="sensor",
                protocol="zigbee",
                capabilities=["temperature", "humidity"],
                meta={"room": f"room-{i % 10}", "floor": i % 3},
            )
        await db_session.commit()
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  Registry create 100: {elapsed:.4f}s ({rate:.0f} devices/sec)")
        assert rate > 20

    @pytest.mark.asyncio
    async def test_create_500(self, db_session: AsyncSession) -> None:
        registry = DeviceRegistry(db_session)
        count = 500

        start = time.perf_counter()
        for i in range(count):
            await registry.create(
                name=f"Device {i}",
                type="actuator" if i % 2 else "sensor",
                protocol="wifi",
                capabilities=["on_off"],
                meta={"idx": i},
            )
        await db_session.commit()
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  Registry create 500: {elapsed:.4f}s ({rate:.0f} devices/sec)")
        assert rate > 10


class TestRegistryRead:
    """Benchmark device read operations."""

    @pytest.mark.asyncio
    async def test_get_all_100(self, db_session: AsyncSession) -> None:
        registry = DeviceRegistry(db_session)
        # Seed
        for i in range(100):
            await registry.create(
                name=f"Dev {i}", type="sensor", protocol="ble",
                capabilities=[], meta={},
            )
        await db_session.commit()

        count = 200
        start = time.perf_counter()
        for _ in range(count):
            devices = await registry.get_all()
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        n = len(devices)
        print(f"\n  Registry get_all ({n} devices) x200: {elapsed:.4f}s ({rate:.0f} q/sec)")
        assert rate > 50

    @pytest.mark.asyncio
    async def test_get_by_id(self, db_session: AsyncSession) -> None:
        registry = DeviceRegistry(db_session)
        device = await registry.create(
            name="Target", type="sensor", protocol="zigbee",
            capabilities=["motion"], meta={},
        )
        await db_session.commit()
        device_id = device.device_id

        count = 500
        start = time.perf_counter()
        for _ in range(count):
            d = await registry.get(device_id)
            assert d is not None
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  Registry get by ID x500: {elapsed:.4f}s ({rate:.0f} q/sec)")
        assert rate > 100

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, db_session: AsyncSession) -> None:
        registry = DeviceRegistry(db_session)
        count = 500
        start = time.perf_counter()
        for _ in range(count):
            d = await registry.get("nonexistent-id-12345")
            assert d is None
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  Registry get nonexistent x500: {elapsed:.4f}s ({rate:.0f} q/sec)")
        assert rate > 100


class TestRegistryUpdate:
    """Benchmark state updates."""

    @pytest.mark.asyncio
    async def test_update_state_100(self, db_session: AsyncSession) -> None:
        registry = DeviceRegistry(db_session)
        device = await registry.create(
            name="Updatable", type="actuator", protocol="wifi",
            capabilities=["on_off", "brightness"], meta={},
        )
        await db_session.commit()
        device_id = device.device_id

        count = 100
        start = time.perf_counter()
        for i in range(count):
            await registry.update_state(
                device_id,
                {"on": i % 2 == 0, "brightness": i % 100, "ts": i},
            )
        await db_session.commit()
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  Registry update_state x100: {elapsed:.4f}s ({rate:.0f} updates/sec)")
        assert rate > 10


class TestRegistryDelete:
    """Benchmark device deletion."""

    @pytest.mark.asyncio
    async def test_delete_50(self, db_session: AsyncSession) -> None:
        registry = DeviceRegistry(db_session)
        ids = []
        for i in range(50):
            d = await registry.create(
                name=f"ToDelete {i}", type="sensor", protocol="ble",
                capabilities=[], meta={},
            )
            ids.append(d.device_id)
        await db_session.commit()

        start = time.perf_counter()
        for did in ids:
            await registry.delete(did)
        await db_session.commit()
        elapsed = time.perf_counter() - start
        rate = 50 / elapsed
        print(f"\n  Registry delete 50: {elapsed:.4f}s ({rate:.0f} del/sec)")
        assert rate > 10
