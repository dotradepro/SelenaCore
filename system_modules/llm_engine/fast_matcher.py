"""
system_modules/llm_engine/fast_matcher.py — Fast Matcher (rule-based intent detection)

Matches user input against keyword/regex rules defined in YAML config.
This is the first tier of the Intent Router — zero-latency, no LLM.

Config file format (YAML):
  intents:
    - name: "turn_on_light"
      keywords: ["включи свет", "turn on light"]
      regex: ["включи .+ свет", "зажги .+"]
      response: "Включаю {device}"
      action:
        type: "device.update_state"
        state: {"on": true}
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

RULES_FILE = os.environ.get("FAST_MATCHER_RULES", "/opt/selena-core/config/intent_rules.yaml")


class MatchResult:
    def __init__(self, intent: str, response: str | None, action: dict | None, score: float) -> None:
        self.intent = intent
        self.response = response
        self.action = action
        self.score = score

    def __bool__(self) -> bool:
        return self.intent != ""


class FastMatcher:
    """Keyword/regex rule-based intent matcher."""

    def __init__(self, rules_file: str = RULES_FILE) -> None:
        self._rules_file = rules_file
        self._rules: list[dict[str, Any]] = []
        self._load_rules()

    def _load_rules(self) -> None:
        path = Path(self._rules_file)
        if not path.exists():
            logger.debug("Fast Matcher rules file not found: %s", self._rules_file)
            self._rules = self._default_rules()
            return

        try:
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            self._rules = data.get("intents", [])
            logger.info("Fast Matcher: loaded %d intent rules", len(self._rules))
        except Exception as e:
            logger.error("Fast Matcher: failed to load rules: %s", e)
            self._rules = self._default_rules()

    def reload(self) -> None:
        """Reload rules from disk."""
        self._load_rules()

    def match(self, text: str) -> MatchResult | None:
        """Match text against all rules. Returns best MatchResult or None."""
        text_lower = text.lower().strip()
        if not text_lower:
            return None

        for rule in self._rules:
            intent_name = rule.get("name", "")

            # Keyword match
            for kw in rule.get("keywords", []):
                if kw.lower() in text_lower:
                    logger.debug("Fast Matcher: keyword match '%s' → '%s'", kw, intent_name)
                    return MatchResult(
                        intent=intent_name,
                        response=rule.get("response"),
                        action=rule.get("action"),
                        score=1.0,
                    )

            # Regex match
            for pattern in rule.get("regex", []):
                if re.search(pattern, text_lower, re.IGNORECASE):
                    logger.debug("Fast Matcher: regex match '%s' → '%s'", pattern, intent_name)
                    return MatchResult(
                        intent=intent_name,
                        response=rule.get("response"),
                        action=rule.get("action"),
                        score=0.9,
                    )

        return None

    @staticmethod
    def _default_rules() -> list[dict]:
        """Built-in rules used when no config file exists."""
        return [
            {
                "name": "turn_on_light",
                "keywords": ["включи свет", "turn on light", "свет включи"],
                "regex": [r"включи .*(свет|лампу|освещение)", r"зажги .+"],
                "response": "Включаю свет",
                "action": {"type": "device.update_state", "state": {"on": True}},
            },
            {
                "name": "turn_off_light",
                "keywords": ["выключи свет", "turn off light", "свет выключи"],
                "regex": [r"выключи .*(свет|лампу|освещение)", r"погаси .+"],
                "response": "Выключаю свет",
                "action": {"type": "device.update_state", "state": {"on": False}},
            },
            {
                "name": "temperature_query",
                "keywords": ["температура", "сколько градусов", "how hot", "temperature"],
                "regex": [r"какая .* температура", r"сколько .* градусов"],
                "response": "Запрашиваю показания температуры",
                "action": {"type": "device.read_state", "capability": "temperature"},
            },
            {
                "name": "privacy_on",
                "keywords": ["режим приватности", "не слушай", "privacy on", "stop listening"],
                "regex": [r"включи.*(приват|не слушай)"],
                "response": "Режим приватности включён",
                "action": {"type": "privacy.enable"},
            },
            {
                "name": "privacy_off",
                "keywords": ["выйди из приватного", "privacy off", "start listening"],
                "regex": [r"выключи.*(приват)"],
                "response": "Режим приватности выключен",
                "action": {"type": "privacy.disable"},
            },
        ]


_matcher: FastMatcher | None = None


def get_fast_matcher() -> FastMatcher:
    global _matcher
    if _matcher is None:
        _matcher = FastMatcher()
    return _matcher
