#!/usr/bin/env python3
"""
scripts/validate_intents.py — Validate IntentCompiler against known test cases.

Compares compiled YAML-based patterns with expected intent matches.
Includes both positive cases (must match) and negative cases (must NOT match).

Run: python scripts/validate_intents.py
"""
from __future__ import annotations

import os
import sys

# Ensure project root is on sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from system_modules.llm_engine.intent_compiler import IntentCompiler


# ── Test cases ───────────────────────────────────────────────────────────
# Format: (phrase, lang, expected_intent, expected_params, is_negative)
#   is_negative=True means the phrase must NOT match expected_intent

TEST_CASES: list[tuple[str, str, str, dict, bool]] = [
    # ── MEDIA ────────────────────────────────────────────────────────────

    # media.volume_set (priority=10)
    ("volume 50",                   "en", "media.volume_set",    {"level": "50"},    False),
    ("set volume to 80",            "en", "media.volume_set",    {"level": "80"},    False),
    ("гучність 70",                 "uk", "media.volume_set",    {"level": "70"},    False),
    ("постав гучність 30",          "uk", "media.volume_set",    {"level": "30"},    False),

    # media.play_genre (priority=10)
    ("play jazz radio",             "en", "media.play_genre",    {"genre": "jazz"},  False),
    ("play rock music",             "en", "media.play_genre",    {"genre": "rock"},  False),
    ("увімкни джаз",                "uk", "media.play_genre",    {"genre": "джаз"},  False),
    ("постав рок музику",           "uk", "media.play_genre",    {"genre": "рок"},   False),

    # media.play_radio_name (priority=10)
    # Note: IntentCompiler matches on lowercased text, so params are lowercase
    ("play station BBC Radio 1",    "en", "media.play_radio_name", {"station_name": "bbc radio 1"}, False),
    ("увімкни радіо Промінь",       "uk", "media.play_radio_name", {"station_name": "промінь"}, False),

    # media.play_search (priority=10)
    ("find bohemian rhapsody",      "en", "media.play_search",   {"query": "bohemian rhapsody"}, False),
    ("знайди океан ельзи",          "uk", "media.play_search",   {"query": "океан ельзи"}, False),

    # media.play_radio (priority=5)
    ("play radio",                  "en", "media.play_radio",    {},                 False),
    ("turn on music",               "en", "media.play_radio",    {},                 False),
    ("увімкни радіо",               "uk", "media.play_radio",    {},                 False),
    ("включи музику",               "uk", "media.play_radio",    {},                 False),

    # media.pause
    ("pause",                       "en", "media.pause",         {},                 False),
    ("пауза",                       "uk", "media.pause",         {},                 False),
    ("на паузу",                    "uk", "media.pause",         {},                 False),

    # media.resume
    ("resume",                      "en", "media.resume",        {},                 False),
    ("продовжи",                    "uk", "media.resume",        {},                 False),

    # media.stop
    ("stop",                        "en", "media.stop",          {},                 False),
    ("stop the music",              "en", "media.stop",          {},                 False),
    ("стоп",                        "uk", "media.stop",          {},                 False),
    ("досить",                      "uk", "media.stop",          {},                 False),

    # media.next
    ("next",                        "en", "media.next",          {},                 False),
    ("skip",                        "en", "media.next",          {},                 False),
    ("наступний",                   "uk", "media.next",          {},                 False),

    # media.previous
    ("previous",                    "en", "media.previous",      {},                 False),
    ("go back",                     "en", "media.previous",      {},                 False),
    ("назад",                       "uk", "media.previous",      {},                 False),

    # media.volume_up
    ("louder",                      "en", "media.volume_up",     {},                 False),
    ("volume up",                   "en", "media.volume_up",     {},                 False),
    ("гучніше",                     "uk", "media.volume_up",     {},                 False),

    # media.volume_down
    ("quieter",                     "en", "media.volume_down",   {},                 False),
    ("volume down",                 "en", "media.volume_down",   {},                 False),
    ("тихіше",                      "uk", "media.volume_down",   {},                 False),

    # media.whats_playing
    ("what's playing",              "en", "media.whats_playing", {},                 False),
    ("що грає",                     "uk", "media.whats_playing", {},                 False),

    # media.shuffle_toggle
    ("shuffle",                     "en", "media.shuffle_toggle",{},                 False),
    ("перемішай",                   "uk", "media.shuffle_toggle",{},                 False),

    # ── WEATHER ──────────────────────────────────────────────────────────

    # weather.current
    ("what's the weather",          "en", "weather.current",     {},                 False),
    ("how's the weather",           "en", "weather.current",     {},                 False),
    ("яка погода",                  "uk", "weather.current",     {},                 False),
    ("що з погодою",                "uk", "weather.current",     {},                 False),

    # weather.forecast
    ("weather forecast",            "en", "weather.forecast",    {},                 False),
    ("forecast for tomorrow",       "en", "weather.forecast",    {"period": "tomorrow"}, False),
    ("прогноз погоди",              "uk", "weather.forecast",    {},                 False),

    # weather.temperature
    ("what's the temperature",      "en", "weather.temperature", {},                 False),
    ("how cold is it",              "en", "weather.temperature", {},                 False),
    ("скільки градусів",            "uk", "weather.temperature", {},                 False),

    # ── AUTOMATION ───────────────────────────────────────────────────────

    ("list automations",            "en", "automation.list",     {},                 False),
    ("які автоматизації",           "uk", "automation.list",     {},                 False),
    ("enable automation morning",   "en", "automation.enable",   {"name": "morning"}, False),
    ("disable automation night",    "en", "automation.disable",  {"name": "night"},  False),
    ("automation status",           "en", "automation.status",   {},                 False),

    # ── PRESENCE ─────────────────────────────────────────────────────────

    ("is John at home",             "en", "presence.check_user", {"name": "john"},   False),
    ("who's home",                  "en", "presence.who_home",   {},                 False),
    ("хто вдома",                   "uk", "presence.who_home",   {},                 False),
    ("presence status",             "en", "presence.status",     {},                 False),

    # ── WATCHDOG ─────────────────────────────────────────────────────────

    ("device status",               "en", "watchdog.status",     {},                 False),
    ("scan devices",                "en", "watchdog.scan",       {},                 False),
    ("перевір пристрої",            "uk", "watchdog.scan",       {},                 False),

    # ── ENERGY ───────────────────────────────────────────────────────────

    ("power consumption",           "en", "energy.current",      {},                 False),
    ("скільки електрики",           "uk", "energy.current",      {},                 False),
    ("energy today",                "en", "energy.today",        {},                 False),

    # ══════════════════════════════════════════════════════════════════════
    # NEGATIVE CASES — must NOT match the specified intent
    # ══════════════════════════════════════════════════════════════════════

    # "stop jazz" should NOT match play_genre (it's STOP, not PLAY)
    ("stop jazz",                   "en", "media.play_genre",    {},                 True),

    # "turn off" should NOT match volume_up
    ("turn it off",                 "en", "media.volume_up",     {},                 True),

    # random text should NOT match any media intent
    ("tell me a joke",              "en", "media.play_radio",    {},                 True),

    # "who is home" should NOT match check_user (no name param)
    ("who is home",                 "en", "presence.check_user", {},                 True),
]


def run() -> int:
    """Run validation and return exit code (0=success, 1=failures)."""
    config_dir = os.path.join(project_root, "config", "intents")
    compiler = IntentCompiler(config_dir)
    compiler.load(["en", "uk"])

    errors: list[str] = []
    passed = 0

    for phrase, lang, expected_intent, expected_params, is_negative in TEST_CASES:
        result = compiler.match(phrase, lang)

        if is_negative:
            # Must NOT match expected_intent
            if result and result["intent"] == expected_intent:
                errors.append(
                    f"  FALSE POSITIVE: '{phrase}' ({lang}) matched {expected_intent} "
                    f"but should NOT",
                )
            else:
                passed += 1
        else:
            # Must match expected_intent
            if not result:
                errors.append(f"  MISS: '{phrase}' ({lang}) — no match (expected {expected_intent})")
            elif result["intent"] != expected_intent:
                errors.append(
                    f"  WRONG: '{phrase}' ({lang}) — got {result['intent']} "
                    f"(expected {expected_intent})",
                )
            else:
                # Check params
                for k, v in expected_params.items():
                    actual = result.get("params", {}).get(k)
                    if actual != v:
                        errors.append(
                            f"  PARAM: '{phrase}' ({lang}) — {k}={actual!r} "
                            f"(expected {v!r})",
                        )
                    else:
                        passed += 1
                if not expected_params:
                    passed += 1

    # Report
    total = len(TEST_CASES)
    intent_count = len(compiler.get_all_definitions())
    module_count = len(compiler.get_all_modules())

    print(f"\nIntentCompiler validation: {intent_count} intents, {module_count} modules")
    print(f"Test cases: {total} total, {passed} passed, {len(errors)} failed\n")

    if errors:
        print("FAILURES:")
        for e in errors:
            print(e)
        print()
        return 1

    print("All tests passed!")
    return 0


if __name__ == "__main__":
    sys.exit(run())
