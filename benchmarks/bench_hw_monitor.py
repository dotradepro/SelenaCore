"""
benchmarks/bench_hw_monitor.py — HW Monitor benchmarks

Tests:
  - collect_metrics() speed
  - Individual sensor read speeds (CPU temp, RAM, disk)
  - Metrics serialization overhead
"""
from __future__ import annotations

import time

import pytest

from system_modules.hw_monitor.monitor import (
    collect_metrics,
    read_cpu_temp,
    read_disk,
    read_ram,
)


class TestMetricsCollection:
    """Benchmark hardware metrics collection."""

    def test_collect_metrics_100(self) -> None:
        count = 100
        start = time.perf_counter()
        for _ in range(count):
            m = collect_metrics()
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  collect_metrics() x100: {elapsed:.4f}s ({rate:.0f} calls/sec)")
        print(f"    CPU temp: {m.cpu_temp_c}°C")
        print(f"    RAM: {m.ram_used_pct:.1f}% ({m.ram_used_mb:.0f}/{m.ram_total_mb:.0f} MB)")
        print(f"    Disk: {m.disk_used_pct:.1f}% ({m.disk_free_gb:.1f} GB free)")
        assert rate > 10

    def test_collect_metrics_1000(self) -> None:
        count = 1_000
        start = time.perf_counter()
        for _ in range(count):
            collect_metrics()
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  collect_metrics() x1000: {elapsed:.4f}s ({rate:.0f} calls/sec)")
        assert rate > 50


class TestIndividualSensors:
    """Benchmark individual sensor reads."""

    def test_read_cpu_temp_1000(self) -> None:
        count = 1_000
        start = time.perf_counter()
        for _ in range(count):
            read_cpu_temp()
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        temp = read_cpu_temp()
        print(f"\n  read_cpu_temp() x1000: {elapsed:.4f}s ({rate:.0f}/sec) = {temp}°C")

    def test_read_ram_1000(self) -> None:
        count = 1_000
        start = time.perf_counter()
        for _ in range(count):
            read_ram()
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        pct, used, total = read_ram()
        print(f"\n  read_ram() x1000: {elapsed:.4f}s ({rate:.0f}/sec) = {pct:.1f}%")

    def test_read_disk_1000(self) -> None:
        count = 1_000
        start = time.perf_counter()
        for _ in range(count):
            read_disk()
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        pct, free = read_disk()
        print(f"\n  read_disk() x1000: {elapsed:.4f}s ({rate:.0f}/sec) = {pct:.1f}%")


class TestMetricsSerialization:
    """Benchmark metrics to dict conversion."""

    def test_to_dict_10k(self) -> None:
        m = collect_metrics()
        count = 10_000
        start = time.perf_counter()
        for _ in range(count):
            d = {
                "cpu_temp_c": m.cpu_temp_c,
                "ram_used_pct": m.ram_used_pct,
                "ram_total_mb": m.ram_total_mb,
                "disk_used_pct": m.disk_used_pct,
                "disk_free_gb": m.disk_free_gb,
            }
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  Metrics to dict x10K: {elapsed:.4f}s ({rate:.0f}/sec)")
        assert rate > 100_000
