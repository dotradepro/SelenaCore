"""
system_modules/hw_monitor/monitor.py — CPU temp + RAM + disk monitoring + alerts

Reads hardware metrics and publishes them to the EventBus.
Triggers alerts when thresholds are exceeded.
Also implements RAM degradation strategy: auto-stop low-priority modules when RAM is critical.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Alert thresholds
CPU_TEMP_WARN = float(os.environ.get("CPU_TEMP_WARN", "70.0"))   # °C
CPU_TEMP_CRIT = float(os.environ.get("CPU_TEMP_CRIT", "85.0"))
RAM_WARN_PCT  = float(os.environ.get("RAM_WARN_PCT",  "80.0"))   # %
RAM_CRIT_PCT  = float(os.environ.get("RAM_CRIT_PCT",  "92.0"))
DISK_WARN_PCT = float(os.environ.get("DISK_WARN_PCT", "85.0"))
DISK_CRIT_PCT = float(os.environ.get("DISK_CRIT_PCT", "95.0"))
MONITOR_INTERVAL = int(os.environ.get("MONITOR_INTERVAL", "30"))  # seconds


@dataclass
class SystemMetrics:
    cpu_temp_c: float | None
    ram_used_pct: float
    ram_used_mb: float
    ram_total_mb: float
    disk_used_pct: float
    disk_free_gb: float


def read_cpu_temp() -> float | None:
    """Read CPU temperature from Linux thermal zone or vcgencmd (RPi)."""
    # Linux generic thermal zone
    for zone in sorted(os.listdir("/sys/class/thermal/")):
        if zone.startswith("thermal_zone"):
            try:
                temp_raw = int(open(f"/sys/class/thermal/{zone}/temp").read().strip())
                return temp_raw / 1000.0
            except Exception:
                continue
    # Raspberry Pi vcgencmd
    try:
        import subprocess
        out = subprocess.check_output(["vcgencmd", "measure_temp"], timeout=2, text=True)
        return float(out.strip().replace("temp=", "").replace("'C", ""))
    except Exception:
        pass
    return None


def read_ram() -> tuple[float, float, float]:
    """Return (used_pct, used_mb, total_mb) using /proc/meminfo."""
    try:
        meminfo: dict[str, int] = {}
        for line in open("/proc/meminfo"):
            parts = line.split()
            if len(parts) >= 2:
                meminfo[parts[0].rstrip(":")] = int(parts[1])
        total_kb = meminfo.get("MemTotal", 0)
        available_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
        used_kb = total_kb - available_kb
        used_pct = (used_kb / total_kb * 100) if total_kb else 0.0
        return used_pct, used_kb / 1024, total_kb / 1024
    except Exception:
        pass
    try:
        import psutil  # type: ignore
        vm = psutil.virtual_memory()
        return vm.percent, vm.used / 1e6, vm.total / 1e6
    except ImportError:
        return 0.0, 0.0, 0.0


def read_disk(path: str = "/") -> tuple[float, float]:
    """Return (used_pct, free_gb)."""
    try:
        usage = shutil.disk_usage(path)
        used_pct = usage.used / usage.total * 100
        free_gb = usage.free / 1e9
        return used_pct, free_gb
    except Exception:
        return 0.0, 0.0


def collect_metrics() -> SystemMetrics:
    cpu_temp = read_cpu_temp()
    ram_pct, ram_used_mb, ram_total_mb = read_ram()
    disk_pct, disk_free_gb = read_disk()
    return SystemMetrics(
        cpu_temp_c=cpu_temp,
        ram_used_pct=ram_pct,
        ram_used_mb=ram_used_mb,
        ram_total_mb=ram_total_mb,
        disk_used_pct=disk_pct,
        disk_free_gb=disk_free_gb,
    )


async def _auto_stop_modules_for_ram() -> None:
    """Stop lowest-priority non-SYSTEM modules when RAM is critical."""
    try:
        from core.module_loader.sandbox import DockerSandbox, ModuleStatus
        sandbox = DockerSandbox()
        modules = sandbox.list_modules()
        running = [
            m for m in modules
            if m.status == ModuleStatus.RUNNING and m.manifest.get("type") != "SYSTEM"
        ]
        # Sort by priority ascending (lowest priority first)
        running.sort(key=lambda m: m.manifest.get("priority", 50))
        if running:
            victim = running[0]
            logger.warning("RAM critical: auto-stopping module %s", victim.name)
            await sandbox.stop(victim.name)
    except Exception as exc:
        logger.error("Auto-stop RAM relief failed: %s", exc)


async def monitor_loop(event_bus=None) -> None:
    """Background monitoring loop. Publishes metrics to EventBus."""
    logger.info("HW monitor started (interval=%ds)", MONITOR_INTERVAL)
    while True:
        await asyncio.sleep(MONITOR_INTERVAL)
        try:
            m = collect_metrics()
            if event_bus:
                await event_bus.publish(
                    "monitor.metrics",
                    {
                        "cpu_temp_c": m.cpu_temp_c,
                        "ram_used_pct": m.ram_used_pct,
                        "ram_total_mb": m.ram_total_mb,
                        "disk_used_pct": m.disk_used_pct,
                        "disk_free_gb": m.disk_free_gb,
                    },
                )

            # CPU temp alerts
            if m.cpu_temp_c is not None:
                if m.cpu_temp_c >= CPU_TEMP_CRIT and event_bus:
                    await event_bus.publish("monitor.alert", {
                        "level": "critical", "metric": "cpu_temp",
                        "value": m.cpu_temp_c, "threshold": CPU_TEMP_CRIT,
                    })
                elif m.cpu_temp_c >= CPU_TEMP_WARN and event_bus:
                    await event_bus.publish("monitor.alert", {
                        "level": "warning", "metric": "cpu_temp",
                        "value": m.cpu_temp_c, "threshold": CPU_TEMP_WARN,
                    })

            # RAM alerts + degradation strategy
            if m.ram_used_pct >= RAM_CRIT_PCT:
                if event_bus:
                    await event_bus.publish("monitor.alert", {
                        "level": "critical", "metric": "ram",
                        "value": m.ram_used_pct, "threshold": RAM_CRIT_PCT,
                    })
                await _auto_stop_modules_for_ram()
            elif m.ram_used_pct >= RAM_WARN_PCT and event_bus:
                await event_bus.publish("monitor.alert", {
                    "level": "warning", "metric": "ram",
                    "value": m.ram_used_pct, "threshold": RAM_WARN_PCT,
                })

            # Disk alerts
            if m.disk_used_pct >= DISK_CRIT_PCT and event_bus:
                await event_bus.publish("monitor.alert", {
                    "level": "critical", "metric": "disk",
                    "value": m.disk_used_pct, "threshold": DISK_CRIT_PCT,
                })
            elif m.disk_used_pct >= DISK_WARN_PCT and event_bus:
                await event_bus.publish("monitor.alert", {
                    "level": "warning", "metric": "disk",
                    "value": m.disk_used_pct, "threshold": DISK_WARN_PCT,
                })

        except Exception as exc:
            logger.error("Monitor loop error: %s", exc)
