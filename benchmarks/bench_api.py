"""
benchmarks/bench_api.py — API endpoint performance benchmarks

Tests:
  - GET /api/v1/health throughput
  - GET /api/v1/devices throughput
  - POST /api/v1/devices create throughput
  - GET /api/v1/system/info throughput
  - Rate limiter overhead
"""
from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


class TestHealthEndpoint:
    """Benchmark /api/v1/health endpoint."""

    @pytest.mark.asyncio
    async def test_health_sequential_100(self, client: AsyncClient) -> None:
        count = 100
        start = time.perf_counter()
        for _ in range(count):
            resp = await client.get("/api/v1/health")
            assert resp.status_code == 200
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  GET /health sequential 100: {elapsed:.4f}s ({rate:.0f} req/sec)")
        assert rate > 50

    @pytest.mark.asyncio
    async def test_health_concurrent_50(self, client: AsyncClient) -> None:
        count = 50

        async def _req():
            return await client.get("/api/v1/health")

        start = time.perf_counter()
        results = await asyncio.gather(*[_req() for _ in range(count)])
        elapsed = time.perf_counter() - start
        rate = count / elapsed

        ok = sum(1 for r in results if r.status_code == 200)
        print(f"\n  GET /health concurrent 50: {elapsed:.4f}s ({rate:.0f} req/sec), ok={ok}")
        assert ok >= count * 0.8  # allow some rate-limited


class TestDevicesEndpoint:
    """Benchmark /api/v1/devices endpoints."""

    @pytest.mark.asyncio
    async def test_list_devices_empty(self, client: AsyncClient, auth_headers: dict) -> None:
        count = 100
        start = time.perf_counter()
        for _ in range(count):
            resp = await client.get("/api/v1/devices", headers=auth_headers)
            assert resp.status_code == 200
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  GET /devices (empty) 100: {elapsed:.4f}s ({rate:.0f} req/sec)")
        assert rate > 50

    @pytest.mark.asyncio
    async def test_create_devices_50(self, client: AsyncClient, auth_headers: dict) -> None:
        count = 50
        start = time.perf_counter()
        for i in range(count):
            resp = await client.post(
                "/api/v1/devices",
                headers=auth_headers,
                json={
                    "name": f"Bench Light {i}",
                    "type": "actuator",
                    "protocol": "zigbee",
                    "capabilities": ["on_off", "brightness"],
                    "meta": {"room": "bench", "idx": i},
                },
            )
            assert resp.status_code in (200, 201)
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  POST /devices create 50: {elapsed:.4f}s ({rate:.0f} req/sec)")
        assert rate > 10

    @pytest.mark.asyncio
    async def test_list_devices_with_data(self, client: AsyncClient, auth_headers: dict) -> None:
        # Seed 20 devices
        for i in range(20):
            await client.post(
                "/api/v1/devices",
                headers=auth_headers,
                json={
                    "name": f"Sensor {i}",
                    "type": "sensor",
                    "protocol": "wifi",
                    "capabilities": ["temperature"],
                    "meta": {},
                },
            )

        count = 100
        start = time.perf_counter()
        for _ in range(count):
            resp = await client.get("/api/v1/devices", headers=auth_headers)
            assert resp.status_code == 200
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        data = resp.json()
        n_devices = len(data.get("devices", data if isinstance(data, list) else []))
        print(f"\n  GET /devices ({n_devices} devices) 100: {elapsed:.4f}s ({rate:.0f} req/sec)")
        assert rate > 30


class TestSystemEndpoint:
    """Benchmark /api/v1/system endpoints."""

    @pytest.mark.asyncio
    async def test_system_info_50(self, client: AsyncClient, auth_headers: dict) -> None:
        count = 50
        statuses: dict[int, int] = {}
        start = time.perf_counter()
        for _ in range(count):
            resp = await client.get("/api/v1/system/info", headers=auth_headers)
            statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  GET /system/info 50: {elapsed:.4f}s ({rate:.0f} req/sec)")
        print(f"    Status codes: {statuses}")


class TestConcurrentMixedLoad:
    """Simulate mixed API load."""

    @pytest.mark.asyncio
    async def test_mixed_load_100(self, client: AsyncClient, auth_headers: dict) -> None:
        """Concurrent mix of health, devices, and system calls."""

        async def _health():
            return ("health", await client.get("/api/v1/health"))

        async def _devices():
            return ("devices", await client.get("/api/v1/devices", headers=auth_headers))

        async def _create():
            return ("create", await client.post(
                "/api/v1/devices",
                headers=auth_headers,
                json={
                    "name": "Mixed Load Device",
                    "type": "sensor",
                    "protocol": "ble",
                    "capabilities": ["humidity"],
                    "meta": {},
                },
            ))

        tasks = []
        for i in range(100):
            match i % 3:
                case 0:
                    tasks.append(_health())
                case 1:
                    tasks.append(_devices())
                case 2:
                    tasks.append(_create())

        start = time.perf_counter()
        results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start

        stats: dict[str, dict[int, int]] = {}
        for name, resp in results:
            if name not in stats:
                stats[name] = {}
            stats[name][resp.status_code] = stats[name].get(resp.status_code, 0) + 1

        rate = 100 / elapsed
        print(f"\n  Mixed load 100 requests: {elapsed:.4f}s ({rate:.0f} req/sec)")
        for name, codes in stats.items():
            print(f"    {name}: {codes}")
