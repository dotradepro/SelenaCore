# system_modules/energy_monitor/voice_handler.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .module import EnergyMonitorModule

logger = logging.getLogger(__name__)


class EnergyVoiceHandler:
    def __init__(self, module: "EnergyMonitorModule") -> None:
        self._module = module

    async def handle(self, intent: str, params: dict) -> dict | None:
        monitor = self._module._monitor

        if monitor is None:
            return {"action": "not_running"}

        match intent:
            case "energy.current":
                total_w = monitor.get_total_power()
                devices = monitor.get_current_power()
                count = len(devices)
                return {"action": "current", "watts": round(total_w), "count": count}

            case "energy.today":
                kwh = monitor.get_total_today_kwh()
                return {"action": "today", "kwh": round(kwh, 2)}

            case _:
                logger.debug("EnergyVoiceHandler: unhandled intent '%s'", intent)
                return None
