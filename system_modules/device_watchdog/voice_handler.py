# system_modules/device_watchdog/voice_handler.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .module import DeviceWatchdogModule

logger = logging.getLogger(__name__)


class WatchdogVoiceHandler:
    def __init__(self, module: "DeviceWatchdogModule") -> None:
        self._module = module

    async def handle(self, intent: str, params: dict) -> None:
        watchdog = self._module._watchdog
        m = self._module

        if watchdog is None:
            await m.speak("Device watchdog is not running.")
            return

        match intent:
            case "watchdog.status":
                summary = watchdog.get_status_summary()
                total = summary.get("total", 0)
                online = summary.get("online", 0)
                offline = summary.get("offline", 0)
                if total == 0:
                    await m.speak("No devices are being monitored.")
                elif offline == 0:
                    await m.speak(
                        f"All {total} devices are online."
                    )
                else:
                    await m.speak(
                        f"{online} of {total} devices online, "
                        f"{offline} offline."
                    )

            case "watchdog.scan":
                result = await watchdog.check_now()
                total = result.get("total", 0)
                online = result.get("online", 0)
                offline = result.get("offline", 0)
                await m.speak(
                    f"Scan complete. {online} online, {offline} offline "
                    f"out of {total} devices."
                )

            case _:
                logger.debug(
                    "WatchdogVoiceHandler: unhandled intent '%s'", intent
                )
