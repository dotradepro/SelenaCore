# system_modules/energy_monitor/voice_handler.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.i18n import t

if TYPE_CHECKING:
    from .module import EnergyMonitorModule

logger = logging.getLogger(__name__)


class EnergyVoiceHandler:
    def __init__(self, module: "EnergyMonitorModule") -> None:
        self._module = module

    async def handle(self, intent: str, params: dict) -> None:
        monitor = self._module._monitor
        m = self._module

        if monitor is None:
            await m.speak(t("energy.not_running"))
            return

        match intent:
            case "energy.current":
                total_w = monitor.get_total_power()
                devices = monitor.get_current_power()
                count = len(devices)
                await m.speak(t("energy.current", watts=f"{total_w:.0f}", count=count))

            case "energy.today":
                kwh = monitor.get_total_today_kwh()
                await m.speak(t("energy.today", kwh=f"{kwh:.2f}"))

            case _:
                logger.debug("EnergyVoiceHandler: unhandled intent '%s'", intent)
