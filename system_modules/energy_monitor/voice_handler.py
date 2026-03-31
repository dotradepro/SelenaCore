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

    async def handle(self, intent: str, params: dict) -> None:
        monitor = self._module._monitor
        m = self._module

        if monitor is None:
            await m.speak("Energy monitor is not running.")
            return

        match intent:
            case "energy.current":
                total_w = monitor.get_total_power()
                devices = monitor.get_current_power()
                count = len(devices)
                await m.speak(
                    f"Current consumption is {total_w:.0f} watts "
                    f"across {count} device{'s' if count != 1 else ''}."
                )

            case "energy.today":
                kwh = monitor.get_total_today_kwh()
                await m.speak(
                    f"Today's total energy consumption is {kwh:.2f} kilowatt-hours."
                )

            case _:
                logger.debug(
                    "EnergyVoiceHandler: unhandled intent '%s'", intent
                )
