# system_modules/automation_engine/voice_handler.py
from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from core.i18n import t

if TYPE_CHECKING:
    from .module import AutomationEngineModule

logger = logging.getLogger(__name__)


def _fuzzy_find(name: str, rules: list[dict]) -> dict | None:
    """Find a rule by fuzzy name match. Returns best match or None."""
    best, best_ratio = None, 0.0
    name_lower = name.lower().strip()
    for rule in rules:
        ratio = SequenceMatcher(None, name_lower, rule["name"].lower()).ratio()
        if ratio > best_ratio:
            best, best_ratio = rule, ratio
    return best if best_ratio >= 0.5 else None


class AutomationVoiceHandler:
    def __init__(self, module: "AutomationEngineModule") -> None:
        self._module = module

    async def handle(self, intent: str, params: dict) -> None:
        engine = self._module._engine
        m = self._module

        if engine is None:
            await m.speak(t("automation.not_running"))
            return

        match intent:
            case "automation.list":
                rules = engine.list_rules()
                if not rules:
                    await m.speak(t("automation.no_rules"))
                    return
                names = ", ".join(r["name"] for r in rules)
                await m.speak(t("automation.list", count=len(rules), names=names))

            case "automation.enable":
                name = params.get("name", "")
                rules = engine.list_rules()
                match_rule = _fuzzy_find(name, rules)
                if match_rule is None:
                    await m.speak(t("automation.not_found", name=name))
                    return
                engine.enable_rule(match_rule["id"], True)
                await m.speak(t("automation.enabled", name=match_rule["name"]))

            case "automation.disable":
                name = params.get("name", "")
                rules = engine.list_rules()
                match_rule = _fuzzy_find(name, rules)
                if match_rule is None:
                    await m.speak(t("automation.not_found", name=name))
                    return
                engine.enable_rule(match_rule["id"], False)
                await m.speak(t("automation.disabled", name=match_rule["name"]))

            case "automation.status":
                status = engine.get_status()
                total = status.get("rules_total", 0)
                enabled = status.get("rules_enabled", 0)
                runs = status.get("run_count", 0)
                await m.speak(t("automation.status", total=total, enabled=enabled, runs=runs))

            case _:
                logger.debug("AutomationVoiceHandler: unhandled intent '%s'", intent)
