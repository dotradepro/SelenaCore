# system_modules/presence_detection/voice_handler.py
from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from core.i18n import t

if TYPE_CHECKING:
    from .module import PresenceDetectionModule

logger = logging.getLogger(__name__)


def _fuzzy_find(name: str, users: list[dict]) -> dict | None:
    """Return the best-matching user by name (ratio >= 0.6), or None."""
    name_lower = name.lower().strip()
    best, best_ratio = None, 0.0
    for u in users:
        ratio = SequenceMatcher(None, name_lower, u["name"].lower()).ratio()
        if ratio > best_ratio:
            best, best_ratio = u, ratio
    return best if best_ratio >= 0.6 else None


class PresenceVoiceHandler:
    def __init__(self, module: "PresenceDetectionModule") -> None:
        self._module = module

    async def handle(self, intent: str, params: dict) -> None:
        m = self._module
        detector = m._detector

        if detector is None:
            await m.speak(t("intent.module_unavailable"))
            return

        match intent:
            case "presence.who_home":
                users = detector.list_users()
                home = [u for u in users if u.get("state") == "home"]
                if not users:
                    await m.speak(t("presence.voice.no_users"))
                elif not home:
                    await m.speak(t("presence.voice.nobody_home"))
                elif len(home) == 1:
                    await m.speak(t("presence.voice.one_home", name=home[0]["name"]))
                else:
                    names = ", ".join(u["name"] for u in home)
                    await m.speak(t("presence.voice.who_home", names=names, count=len(home)))

            case "presence.check_user":
                name = params.get("name", "").strip()
                if not name:
                    await m.speak(t("presence.voice.specify_name"))
                    return
                users = detector.list_users()
                user = _fuzzy_find(name, users)
                if user is None:
                    await m.speak(t("presence.voice.user_not_found", name=name))
                elif user.get("state") == "home":
                    await m.speak(t("presence.voice.user_home", name=user["name"]))
                else:
                    await m.speak(t("presence.voice.user_away", name=user["name"]))

            case "presence.status":
                status = detector.get_status()
                total = status.get("users_total", 0)
                home = status.get("users_home", 0)
                away = status.get("users_away", 0)
                await m.speak(t("presence.voice.status", total=total, home=home, away=away))

            case _:
                logger.debug("PresenceVoiceHandler: unhandled intent '%s'", intent)
