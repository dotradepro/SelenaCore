# system_modules/presence_detection/voice_handler.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.utils.fuzzy import fuzzy_find

if TYPE_CHECKING:
    from .module import PresenceDetectionModule

logger = logging.getLogger(__name__)


class PresenceVoiceHandler:
    def __init__(self, module: "PresenceDetectionModule") -> None:
        self._module = module

    async def handle(self, intent: str, params: dict) -> dict | None:
        detector = self._module._detector

        if detector is None:
            return {"action": "module_unavailable"}

        match intent:
            case "presence.who_home":
                users = detector.list_users()
                home = [u for u in users if u.get("state") == "home"]
                if not users:
                    return {"action": "no_users"}
                if not home:
                    return {"action": "nobody_home"}
                names = [u["name"] for u in home]
                return {"action": "who_home", "names": names, "count": len(home)}

            case "presence.check_user":
                name = params.get("name", "").strip()
                if not name:
                    return {"action": "specify_name"}
                users = detector.list_users()
                user = fuzzy_find(name, users, threshold=0.6)
                if user is None:
                    return {"action": "user_not_found", "name": name}
                return {
                    "action": "user_home" if user.get("state") == "home" else "user_away",
                    "name": user["name"],
                }

            case "presence.status":
                status = detector.get_status()
                total = status.get("users_total", 0)
                home = status.get("users_home", 0)
                away = status.get("users_away", 0)
                return {"action": "status", "total": total, "home": home, "away": away}

            case _:
                logger.debug("PresenceVoiceHandler: unhandled intent '%s'", intent)
                return None
