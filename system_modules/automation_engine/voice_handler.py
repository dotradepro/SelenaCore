# system_modules/automation_engine/voice_handler.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.utils.fuzzy import fuzzy_find

if TYPE_CHECKING:
    from .module import AutomationEngineModule

logger = logging.getLogger(__name__)


class AutomationVoiceHandler:
    def __init__(self, module: "AutomationEngineModule") -> None:
        self._module = module

    async def handle(self, intent: str, params: dict) -> dict | None:
        engine = self._module._engine

        if engine is None:
            return {"action": "not_running"}

        match intent:
            case "automation.list":
                rules = engine.list_rules()
                if not rules:
                    return {"action": "no_rules"}
                names = ", ".join(r["name"] for r in rules)
                return {"action": "list", "count": len(rules), "names": names}

            case "automation.enable":
                name = params.get("name", "")
                rules = engine.list_rules()
                match_rule = fuzzy_find(name, rules)
                if match_rule is None:
                    return {"action": "not_found", "name": name}
                engine.enable_rule(match_rule["id"], True)
                return {"action": "enabled", "name": match_rule["name"]}

            case "automation.disable":
                name = params.get("name", "")
                rules = engine.list_rules()
                match_rule = fuzzy_find(name, rules)
                if match_rule is None:
                    return {"action": "not_found", "name": name}
                engine.enable_rule(match_rule["id"], False)
                return {"action": "disabled", "name": match_rule["name"]}

            # automation.status was merged into automation.list
            # (2026-04-18) — list now returns both the rules array and
            # the summary counts so one intent covers both questions.

            case _:
                logger.debug("AutomationVoiceHandler: unhandled intent '%s'", intent)
                return None
