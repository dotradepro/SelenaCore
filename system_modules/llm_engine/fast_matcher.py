"""
system_modules/llm_engine/fast_matcher.py — Fast Matcher (rule-based intent detection)

Matches user input against keyword/regex rules defined in YAML config.
This is the first tier of the Intent Router — zero-latency, no LLM.

Config file format (YAML):
  intents:
    - name: "turn_on_light"
      keywords: ["turn on light", "switch on light"]
      regex: ["turn on .+ light", "switch on .+"]
      response: "Turning on {device}"
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
    def __init__(
        self,
        intent: str,
        response: str | None,
        action: dict | None,
        score: float,
        params: dict[str, str] | None = None,
    ) -> None:
        self.intent = intent
        self.response = response
        self.action = action
        self.score = score
        self.params = params or {}

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

    def match(self, text: str, lang: str | None = None) -> MatchResult | None:
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
                        response=self._resolve_response(rule.get("response"), lang),
                        action=rule.get("action"),
                        score=1.0,
                    )

            # Regex match
            for pattern in rule.get("regex", []):
                m = re.search(pattern, text_lower, re.IGNORECASE)
                if m:
                    logger.debug("Fast Matcher: regex match '%s' → '%s'", pattern, intent_name)
                    return MatchResult(
                        intent=intent_name,
                        response=self._resolve_response(rule.get("response"), lang),
                        action=rule.get("action"),
                        score=0.9,
                        params=m.groupdict() or {},
                    )

        return None

    @staticmethod
    def _resolve_response(response: str | None, lang: str | None) -> str | None:
        """Resolve response through i18n if it looks like a translation key."""
        if not response:
            return response
        # Translation keys use dot-notation (e.g. "fast_matcher.light_on")
        if "." in response and " " not in response:
            from core.i18n import t
            return t(response, lang=lang)
        return response

    @staticmethod
    def _default_rules() -> list[dict]:
        """Built-in rules used when no config file exists."""
        return [
            {
                "name": "turn_on_light",
                "keywords": ["turn on light", "switch on light",
                             "увімкни світло", "вімкни світло", "світло увімкни"],
                "regex": [r"turn on .*(light|lamp)",
                          r"у?вімкни .*(світло|лампу)"],
                "response": "fast_matcher.light_on",
                "action": {"type": "device.update_state", "state": {"on": True}},
            },
            {
                "name": "turn_off_light",
                "keywords": ["turn off light", "switch off light",
                             "вимкни світло", "світло вимкни"],
                "regex": [r"turn off .*(light|lamp)",
                          r"вимкни .*(світло|лампу)"],
                "response": "fast_matcher.light_off",
                "action": {"type": "device.update_state", "state": {"on": False}},
            },
            {
                "name": "temperature_query",
                "keywords": ["temperature", "how hot",
                             "температура", "скільки градусів"],
                "regex": [r"what.* temperature", r"how .* degrees",
                          r"яка .* температура"],
                "response": "fast_matcher.temperature_query",
                "action": {"type": "device.read_state", "capability": "temperature"},
            },
            {
                "name": "privacy_on",
                "keywords": ["privacy on", "stop listening",
                             "не слухай", "режим приватності"],
                "regex": [r"enable.*(privac)", r"у?вімкни.*(приват|не слухай)"],
                "response": "fast_matcher.privacy_on",
                "action": {"type": "privacy.enable"},
            },
            {
                "name": "privacy_off",
                "keywords": ["privacy off", "start listening",
                             "вийди з приватного"],
                "regex": [r"disable.*(privac)", r"вимкни.*(приват)"],
                "response": "fast_matcher.privacy_off",
                "action": {"type": "privacy.disable"},
            },
            # ── Media controls (zero-param, keyword-only for speed) ──
            {
                "name": "media.pause",
                "keywords": ["pause", "пауза", "на паузу"],
                "regex": [],
                "response": "",
            },
            {
                "name": "media.stop",
                "keywords": ["stop", "стоп", "досить"],
                "regex": [r"(?:stop|вимкни)\s*(?:music|музику)"],
                "response": "",
            },
            {
                "name": "media.next",
                "keywords": ["next track", "skip", "наступний трек"],
                "regex": [],
                "response": "",
            },
            {
                "name": "media.previous",
                "keywords": ["previous track", "попередній трек"],
                "regex": [],
                "response": "",
            },
            {
                "name": "media.volume_up",
                "keywords": ["louder", "volume up", "гучніше", "погучніше"],
                "regex": [],
                "response": "",
            },
            {
                "name": "media.volume_down",
                "keywords": ["quieter", "volume down", "тихіше", "потихіше"],
                "regex": [],
                "response": "",
            },
        ]


_matcher: FastMatcher | None = None


def get_fast_matcher() -> FastMatcher:
    global _matcher
    if _matcher is None:
        _matcher = FastMatcher()
    return _matcher
