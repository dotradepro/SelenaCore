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

    async def handle(self, intent: str, params: dict) -> dict | None:
        watchdog = self._module._watchdog

        if watchdog is None:
            return {"action": "not_running"}

        match intent:
            case "watchdog.status":
                summary = watchdog.get_status_summary()
                total = summary.get("total", 0)
                online = summary.get("online", 0)
                offline = summary.get("offline", 0)
                return {"action": "status", "total": total, "online": online, "offline": offline}

            case "watchdog.scan":
                result = await watchdog.check_now()
                total = result.get("total", 0)
                online = result.get("online", 0)
                offline = result.get("offline", 0)
                return {"action": "scan_done", "total": total, "online": online, "offline": offline}

            case _:
                logger.debug("WatchdogVoiceHandler: unhandled intent '%s'", intent)
                return None
