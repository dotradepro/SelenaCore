# system_modules/device_watchdog/voice_handler.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.i18n import t

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
            await m.speak(t("watchdog.not_running"))
            return

        match intent:
            case "watchdog.status":
                summary = watchdog.get_status_summary()
                total = summary.get("total", 0)
                online = summary.get("online", 0)
                offline = summary.get("offline", 0)
                if total == 0:
                    await m.speak(t("watchdog.no_devices"))
                elif offline == 0:
                    await m.speak(t("watchdog.all_online", total=total))
                else:
                    await m.speak(t("watchdog.status", online=online, total=total, offline=offline))

            case "watchdog.scan":
                result = await watchdog.check_now()
                total = result.get("total", 0)
                online = result.get("online", 0)
                offline = result.get("offline", 0)
                await m.speak(t("watchdog.scan_done", online=online, offline=offline, total=total))

            case _:
                logger.debug("WatchdogVoiceHandler: unhandled intent '%s'", intent)
