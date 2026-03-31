"""
benchmarks/server_stats.py — Server load statistics collector

Captures CPU, RAM, disk metrics during benchmark runs and
produces a summary report at the end.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Snapshot:
    timestamp: float
    cpu_pct: float
    ram_used_pct: float
    ram_used_mb: float
    ram_total_mb: float
    disk_used_pct: float
    disk_free_gb: float
    cpu_temp_c: float | None


@dataclass
class ServerStats:
    """Collects periodic server load snapshots during benchmarks."""

    interval: float = 1.0
    _snapshots: list[Snapshot] = field(default_factory=list)
    _task: asyncio.Task | None = field(default=None, repr=False)
    _running: bool = False

    def _read_cpu_usage(self) -> float:
        """Read CPU usage from /proc/stat (delta between two reads)."""
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            parts = line.split()
            idle = int(parts[4])
            total = sum(int(p) for p in parts[1:])
            return idle, total
        except Exception:
            return 0, 1

    def _read_ram(self) -> tuple[float, float, float]:
        try:
            meminfo: dict[str, int] = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        meminfo[parts[0].rstrip(":")] = int(parts[1])
            total_kb = meminfo.get("MemTotal", 0)
            available_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
            used_kb = total_kb - available_kb
            used_pct = (used_kb / total_kb * 100) if total_kb else 0.0
            return used_pct, used_kb / 1024, total_kb / 1024
        except Exception:
            return 0.0, 0.0, 0.0

    def _read_disk(self) -> tuple[float, float]:
        try:
            usage = shutil.disk_usage("/")
            return usage.used / usage.total * 100, usage.free / 1e9
        except Exception:
            return 0.0, 0.0

    def _read_cpu_temp(self) -> float | None:
        try:
            for zone in sorted(os.listdir("/sys/class/thermal/")):
                if zone.startswith("thermal_zone"):
                    raw = int(open(f"/sys/class/thermal/{zone}/temp").read().strip())
                    return raw / 1000.0
        except Exception:
            pass
        return None

    def collect_snapshot(self) -> Snapshot:
        ram_pct, ram_used_mb, ram_total_mb = self._read_ram()
        disk_pct, disk_free_gb = self._read_disk()
        return Snapshot(
            timestamp=time.monotonic(),
            cpu_pct=0.0,  # filled by delta in loop
            ram_used_pct=ram_pct,
            ram_used_mb=ram_used_mb,
            ram_total_mb=ram_total_mb,
            disk_used_pct=disk_pct,
            disk_free_gb=disk_free_gb,
            cpu_temp_c=self._read_cpu_temp(),
        )

    async def _monitor_loop(self) -> None:
        prev_idle, prev_total = self._read_cpu_usage()
        while self._running:
            await asyncio.sleep(self.interval)
            curr_idle, curr_total = self._read_cpu_usage()
            d_idle = curr_idle - prev_idle
            d_total = curr_total - prev_total
            cpu_pct = (1.0 - d_idle / max(d_total, 1)) * 100
            prev_idle, prev_total = curr_idle, curr_total

            snap = self.collect_snapshot()
            snap.cpu_pct = max(0.0, cpu_pct)
            self._snapshots.append(snap)

    async def start(self) -> None:
        self._running = True
        self._snapshots.clear()
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def report(self) -> dict:
        """Generate summary statistics from collected snapshots."""
        if not self._snapshots:
            return {"error": "No snapshots collected"}

        n = len(self._snapshots)
        duration = self._snapshots[-1].timestamp - self._snapshots[0].timestamp

        def _stats(values: list[float]) -> dict:
            if not values:
                return {"min": 0, "max": 0, "avg": 0, "last": 0}
            return {
                "min": round(min(values), 2),
                "max": round(max(values), 2),
                "avg": round(sum(values) / len(values), 2),
                "last": round(values[-1], 2),
            }

        cpu_vals = [s.cpu_pct for s in self._snapshots]
        ram_vals = [s.ram_used_pct for s in self._snapshots]
        ram_mb_vals = [s.ram_used_mb for s in self._snapshots]
        temp_vals = [s.cpu_temp_c for s in self._snapshots if s.cpu_temp_c is not None]

        return {
            "snapshots": n,
            "duration_sec": round(duration, 2),
            "cpu_pct": _stats(cpu_vals),
            "ram_pct": _stats(ram_vals),
            "ram_used_mb": _stats(ram_mb_vals),
            "ram_total_mb": round(self._snapshots[-1].ram_total_mb, 1),
            "disk_used_pct": round(self._snapshots[-1].disk_used_pct, 2),
            "disk_free_gb": round(self._snapshots[-1].disk_free_gb, 2),
            "cpu_temp_c": _stats(temp_vals) if temp_vals else None,
        }

    def print_report(self) -> None:
        """Print formatted report to stdout."""
        r = self.report()
        if "error" in r:
            print(f"  Server Stats: {r['error']}")
            return

        print("\n" + "=" * 70)
        print("  SERVER LOAD STATISTICS")
        print("=" * 70)
        print(f"  Duration: {r['duration_sec']}s | Snapshots: {r['snapshots']}")
        print(f"  RAM Total: {r['ram_total_mb']} MB")
        print("-" * 70)
        print(f"  {'Metric':<20} {'Min':>10} {'Avg':>10} {'Max':>10} {'Last':>10}")
        print("-" * 70)
        print(f"  {'CPU %':<20} {r['cpu_pct']['min']:>10} {r['cpu_pct']['avg']:>10} {r['cpu_pct']['max']:>10} {r['cpu_pct']['last']:>10}")
        print(f"  {'RAM %':<20} {r['ram_pct']['min']:>10} {r['ram_pct']['avg']:>10} {r['ram_pct']['max']:>10} {r['ram_pct']['last']:>10}")
        print(f"  {'RAM MB':<20} {r['ram_used_mb']['min']:>10} {r['ram_used_mb']['avg']:>10} {r['ram_used_mb']['max']:>10} {r['ram_used_mb']['last']:>10}")
        if r['cpu_temp_c']:
            print(f"  {'CPU Temp C':<20} {r['cpu_temp_c']['min']:>10} {r['cpu_temp_c']['avg']:>10} {r['cpu_temp_c']['max']:>10} {r['cpu_temp_c']['last']:>10}")
        print(f"  {'Disk %':<20} {'':>10} {'':>10} {'':>10} {r['disk_used_pct']:>10}")
        print(f"  {'Disk Free GB':<20} {'':>10} {'':>10} {'':>10} {r['disk_free_gb']:>10}")
        print("=" * 70)
