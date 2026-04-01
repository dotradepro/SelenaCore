#!/usr/bin/env python3
"""
scripts/seed_smart_matcher.py — Seed SmartMatcher with example phrases and test thresholds.

Run once before first start, or after adding new intents to definitions.yaml.

Usage:
    python scripts/seed_smart_matcher.py           # seed + test
    python scripts/seed_smart_matcher.py --test     # test only (no seed)
"""
from __future__ import annotations

import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Seed phrases: (text, intent, noun_class, verb, module, lang)
SEED: list[tuple[str, str, str, str, str, str]] = [
    # ── MEDIA ────────────────────────────────────────────────────────────
    ("play jazz radio",               "media.play_genre",     "MEDIA",    "PLAY",  "media-player", "en"),
    ("put on some rock music",        "media.play_genre",     "MEDIA",    "PLAY",  "media-player", "en"),
    ("play classical",                "media.play_genre",     "MEDIA",    "PLAY",  "media-player", "en"),
    ("start playing ambient",         "media.play_genre",     "MEDIA",    "PLAY",  "media-player", "en"),
    ("turn on the radio",             "media.play_radio",     "MEDIA",    "PLAY",  "media-player", "en"),
    ("play some music",               "media.play_radio",     "MEDIA",    "PLAY",  "media-player", "en"),
    ("set volume to 50",              "media.volume_set",     "MEDIA",    "SET",   "media-player", "en"),
    ("volume 80",                     "media.volume_set",     "MEDIA",    "SET",   "media-player", "en"),
    ("pause the music",               "media.pause",          "MEDIA",    "PAUSE", "media-player", "en"),
    ("stop playing",                  "media.stop",           "MEDIA",    "STOP",  "media-player", "en"),
    ("stop the music",                "media.stop",           "MEDIA",    "STOP",  "media-player", "en"),
    ("resume playback",               "media.resume",         "MEDIA",    "RESUME","media-player", "en"),
    ("next track",                    "media.next",           "MEDIA",    "PLAY",  "media-player", "en"),
    ("skip this song",                "media.next",           "MEDIA",    "PLAY",  "media-player", "en"),
    ("previous track",                "media.previous",       "MEDIA",    "PLAY",  "media-player", "en"),
    ("go back",                       "media.previous",       "MEDIA",    "PLAY",  "media-player", "en"),
    ("make it louder",                "media.volume_up",      "MEDIA",    "SET",   "media-player", "en"),
    ("turn it up",                    "media.volume_up",      "MEDIA",    "SET",   "media-player", "en"),
    ("turn up the volume",            "media.volume_up",      "MEDIA",    "SET",   "media-player", "en"),
    ("increase volume",               "media.volume_up",      "MEDIA",    "SET",   "media-player", "en"),
    ("make it quieter",               "media.volume_down",    "MEDIA",    "SET",   "media-player", "en"),
    ("turn it down",                  "media.volume_down",    "MEDIA",    "SET",   "media-player", "en"),
    ("turn down the volume",          "media.volume_down",    "MEDIA",    "SET",   "media-player", "en"),
    ("decrease volume",               "media.volume_down",    "MEDIA",    "SET",   "media-player", "en"),
    ("what is playing right now",     "media.whats_playing",  "MEDIA",    "QUERY", "media-player", "en"),
    ("what song is this",             "media.whats_playing",  "MEDIA",    "QUERY", "media-player", "en"),
    ("shuffle the playlist",          "media.shuffle_toggle", "MEDIA",    "PLAY",  "media-player", "en"),
    ("find a song by queen",          "media.play_search",    "MEDIA",    "SEARCH","media-player", "en"),
    ("play station bbc radio one",    "media.play_radio_name","MEDIA",    "PLAY",  "media-player", "en"),
    # UK media
    ("постав джаз",                   "media.play_genre",     "MEDIA",    "PLAY",  "media-player", "uk"),
    ("увімкни рок музику",            "media.play_genre",     "MEDIA",    "PLAY",  "media-player", "uk"),
    ("увімкни радіо",                 "media.play_radio",     "MEDIA",    "PLAY",  "media-player", "uk"),
    ("включи музику",                 "media.play_radio",     "MEDIA",    "PLAY",  "media-player", "uk"),
    ("запусти музику",                "media.play_radio",     "MEDIA",    "PLAY",  "media-player", "uk"),
    ("гучність 50",                   "media.volume_set",     "MEDIA",    "SET",   "media-player", "uk"),
    ("пауза",                         "media.pause",          "MEDIA",    "PAUSE", "media-player", "uk"),
    ("стоп",                          "media.stop",           "MEDIA",    "STOP",  "media-player", "uk"),
    ("наступний трек",                "media.next",           "MEDIA",    "PLAY",  "media-player", "uk"),
    ("гучніше",                       "media.volume_up",      "MEDIA",    "SET",   "media-player", "uk"),
    ("тихіше",                        "media.volume_down",    "MEDIA",    "SET",   "media-player", "uk"),
    ("що грає",                       "media.whats_playing",  "MEDIA",    "QUERY", "media-player", "uk"),

    # ── WEATHER ──────────────────────────────────────────────────────────
    ("what is the weather like",      "weather.current",      "WEATHER",  "QUERY", "weather-service", "en"),
    ("how is the weather today",      "weather.current",      "WEATHER",  "QUERY", "weather-service", "en"),
    ("weather report",                "weather.current",      "WEATHER",  "QUERY", "weather-service", "en"),
    ("will it rain tomorrow",         "weather.forecast",     "WEATHER",  "QUERY", "weather-service", "en"),
    ("weather forecast for the week", "weather.forecast",     "WEATHER",  "QUERY", "weather-service", "en"),
    ("how cold is it outside",        "weather.temperature",  "WEATHER",  "QUERY", "weather-service", "en"),
    ("what is the temperature now",   "weather.temperature",  "WEATHER",  "QUERY", "weather-service", "en"),
    ("temperature outside",           "weather.temperature",  "WEATHER",  "QUERY", "weather-service", "en"),
    ("weather outside right now",     "weather.current",      "WEATHER",  "QUERY", "weather-service", "en"),
    # UK weather
    ("яка зараз погода",              "weather.current",      "WEATHER",  "QUERY", "weather-service", "uk"),
    ("що з погодою",                  "weather.current",      "WEATHER",  "QUERY", "weather-service", "uk"),
    ("прогноз погоди на завтра",      "weather.forecast",     "WEATHER",  "QUERY", "weather-service", "uk"),
    ("скільки градусів надворі",      "weather.temperature",  "WEATHER",  "QUERY", "weather-service", "uk"),
    ("яка температура зараз",         "weather.temperature",  "WEATHER",  "QUERY", "weather-service", "uk"),

    # ── DEVICE / WATCHDOG ────────────────────────────────────────────────
    ("are all devices working",       "watchdog.status",      "WATCHDOG", "QUERY", "device-watchdog", "en"),
    ("check device connectivity",     "watchdog.scan",        "WATCHDOG", "SCAN",  "device-watchdog", "en"),
    ("статус пристроїв",              "watchdog.status",      "WATCHDOG", "QUERY", "device-watchdog", "uk"),
    ("перевір пристрої",              "watchdog.scan",        "WATCHDOG", "SCAN",  "device-watchdog", "uk"),

    # ── ENERGY ───────────────────────────────────────────────────────────
    ("how much electricity are we using", "energy.current",   "ENERGY",   "QUERY", "energy-monitor", "en"),
    ("current power usage",           "energy.current",       "ENERGY",   "QUERY", "energy-monitor", "en"),
    ("power consumption right now",   "energy.current",       "ENERGY",   "QUERY", "energy-monitor", "en"),
    ("energy used today",             "energy.today",         "ENERGY",   "QUERY", "energy-monitor", "en"),
    ("скільки електрики",             "energy.current",       "ENERGY",   "QUERY", "energy-monitor", "uk"),
    ("споживання за сьогодні",        "energy.today",         "ENERGY",   "QUERY", "energy-monitor", "uk"),

    # ── PRESENCE ─────────────────────────────────────────────────────────
    ("is anyone at home",             "presence.who_home",    "PRESENCE", "QUERY", "presence-detection", "en"),
    ("who is here right now",         "presence.who_home",    "PRESENCE", "QUERY", "presence-detection", "en"),
    ("is mom at home",                "presence.check_user",  "PRESENCE", "QUERY", "presence-detection", "en"),
    ("presence overview",             "presence.status",      "PRESENCE", "QUERY", "presence-detection", "en"),
    ("хто зараз вдома",               "presence.who_home",    "PRESENCE", "QUERY", "presence-detection", "uk"),
    ("статус присутності",            "presence.status",      "PRESENCE", "QUERY", "presence-detection", "uk"),

    # ── AUTOMATION ───────────────────────────────────────────────────────
    ("show all automations",          "automation.list",      "AUTOMATION","LIST", "automation-engine", "en"),
    ("automation rules",              "automation.list",      "AUTOMATION","LIST", "automation-engine", "en"),
    ("turn on morning routine",       "automation.enable",    "AUTOMATION","ON",   "automation-engine", "en"),
    ("disable night mode",            "automation.disable",   "AUTOMATION","OFF",  "automation-engine", "en"),
    ("automation engine status",      "automation.status",    "AUTOMATION","QUERY","automation-engine", "en"),
    ("які автоматизації",             "automation.list",      "AUTOMATION","LIST", "automation-engine", "uk"),
    ("статус автоматизацій",          "automation.status",    "AUTOMATION","QUERY","automation-engine", "uk"),
]

# Positive test cases: (text, expected_intent) — should match
POSITIVE_TESTS: list[tuple[str, str]] = [
    # Phrases NOT in SEED but using similar vocabulary
    ("play some tunes",               "media.play_radio"),
    ("turn up the volume",            "media.volume_up"),
    ("turn down the volume",          "media.volume_down"),
    ("weather outside",               "weather.current"),
    ("power consumption now",         "energy.current"),
    ("who is home now",               "presence.who_home"),
    # UK
    ("запусти музику",                "media.play_radio"),
    ("яка температура",               "weather.temperature"),
]

# Negative test cases: (text,) — should NOT match any intent (score < threshold)
NEGATIVE_TESTS: list[str] = [
    "tell me a joke",
    "who is the president",
    "купи молоко",
    "what time is it",
    "яка сьогодні дата",
    "розкажи казку",
]


def do_seed() -> None:
    """Seed the SmartMatcher index from SEED data."""
    from system_modules.llm_engine.intent_compiler import IntentCompiler
    from system_modules.llm_engine.smart_matcher import SmartMatcher

    config_dir = os.path.join(project_root, "config", "intents")
    data_dir = os.environ.get("CORE_DATA_DIR", "/tmp/selena-test")
    sm_dir = os.path.join(data_dir, "smart_matcher")

    compiler = IntentCompiler(config_dir)
    compiler.load(["en", "uk"])

    matcher = SmartMatcher(data_dir=sm_dir)

    # Build from compiled intents
    all_entries = []
    for module_name in compiler.get_all_modules():
        all_entries.extend(compiler.get_intents_for_module(module_name))
    matcher.build_index(all_entries, compiler.get_all_definitions())

    # Add seed examples
    for text, intent, nc, verb, module, lang in SEED:
        matcher.add_example(text, intent, {
            "noun_class": nc,
            "verb": verb,
            "module": module,
            "source": "seed",
        })

    # Rebuild with seed data
    matcher.rebuild()
    print(f"Seeded SmartMatcher with {len(SEED)} examples ({matcher.entry_count} total entries)")
    return matcher


def do_test(matcher: "SmartMatcher") -> int:
    """Test SmartMatcher thresholds. Returns exit code."""
    from system_modules.llm_engine.structure_extractor import extract_structure

    errors = 0
    passed = 0

    # Positive tests
    print("\n--- Positive tests (should match) ---")
    for text, expected_intent in POSITIVE_TESTS:
        struct = extract_structure(text)
        result = matcher.match(text, struct)
        if result and result["intent"] == expected_intent:
            status = "CONFIDENT" if not result.get("uncertain") else "uncertain"
            print(f"  OK  {text!r:45} -> {result['intent']:25} (score={result['score']:.2f}, {status})")
            passed += 1
        elif result:
            print(f"  WRONG {text!r:43} -> {result['intent']:25} (expected {expected_intent}, score={result['score']:.2f})")
            errors += 1
        else:
            print(f"  MISS  {text!r:43} -> MISS (expected {expected_intent})")
            errors += 1

    # Negative tests
    print("\n--- Negative tests (should NOT match) ---")
    for text in NEGATIVE_TESTS:
        struct = extract_structure(text)
        result = matcher.match(text, struct)
        if result:
            print(f"  FALSE POSITIVE {text!r:35} -> {result['intent']:25} (score={result['score']:.2f})")
            errors += 1
        else:
            print(f"  OK    {text!r:43} -> no match (correct)")
            passed += 1

    print(f"\nResults: {passed} passed, {errors} failed")
    return 1 if errors else 0


def main() -> int:
    test_only = "--test" in sys.argv

    if test_only:
        from system_modules.llm_engine.smart_matcher import SmartMatcher
        from system_modules.llm_engine.intent_compiler import IntentCompiler

        config_dir = os.path.join(project_root, "config", "intents")
        data_dir = os.environ.get("CORE_DATA_DIR", "/tmp/selena-test")
        sm_dir = os.path.join(data_dir, "smart_matcher")

        compiler = IntentCompiler(config_dir)
        compiler.load(["en", "uk"])

        matcher = SmartMatcher(data_dir=sm_dir)
        all_entries = []
        for module_name in compiler.get_all_modules():
            all_entries.extend(compiler.get_intents_for_module(module_name))
        matcher.build_index(all_entries, compiler.get_all_definitions())
        return do_test(matcher)
    else:
        matcher = do_seed()
        return do_test(matcher)


if __name__ == "__main__":
    sys.exit(main())
