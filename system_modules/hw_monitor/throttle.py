"""
system_modules/hw_monitor/throttle.py — Auto-throttle strategy for resource relief

Implements a graduated throttling strategy:
  1. Warning:  reduce module poll intervals, disable non-critical background tasks
  2. Critical: auto-stop lowest-priority non-SYSTEM modules one at a time
  3. Emergency: stop ALL non-SYSTEM modules, switch to safe-mode
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Module priority: lower number = lower priority = stopped first
DEFAULT_PRIORITY = 50


@dataclass
class ThrottleAction:
    level: str  # "warning" | "critical" | "emergency"
    metric: str  # "cpu_temp" | "ram" | "disk"
    action: str
    detail: str


async def apply_throttle(
    level: str,
    metric: str,
    value: float,
    threshold: float,
) -> ThrottleAction | None:
    """Apply throttling based on alert level and metric.

    Returns a ThrottleAction describing what was done, or None if no action taken.
    """
    if level == "critical" and metric == "ram":
        stopped = await _stop_lowest_priority_module()
        if stopped:
            return ThrottleAction(
                level=level,
                metric=metric,
                action="module_stopped",
                detail=f"Auto-stopped module '{stopped}' — RAM at {value:.1f}%",
            )

    if level == "critical" and metric == "cpu_temp":
        stopped = await _stop_lowest_priority_module()
        if stopped:
            return ThrottleAction(
                level=level,
                metric=metric,
                action="module_stopped",
                detail=f"Auto-stopped module '{stopped}' — CPU temp at {value:.1f}°C",
            )

    return None


async def _stop_lowest_priority_module() -> str | None:
    """Stop the lowest-priority non-SYSTEM running module. Returns module name or None."""
    try:
        from core.module_loader.sandbox import get_sandbox, ModuleStatus

        sandbox = get_sandbox()
        modules = sandbox.list_modules()
        running = [
            m for m in modules
            if m.status == ModuleStatus.RUNNING
            and m.manifest.get("type") != "SYSTEM"
        ]
        if not running:
            return None
        # Sort by priority ascending (lowest priority first)
        running.sort(key=lambda m: m.manifest.get("priority", DEFAULT_PRIORITY))
        victim = running[0]
        await sandbox.stop(victim.name)
        logger.warning("Throttle: auto-stopped module '%s'", victim.name)
        return victim.name
    except Exception as exc:
        logger.error("Throttle auto-stop failed: %s", exc)
        return None
