"""
system_modules/weather_service/intent_patterns.py — Voice intent patterns for weather-service.

Registered with IntentRouter on module startup (Tier 1.5: system module intents).
Patterns use regex with optional named groups for parameter extraction.
"""
from __future__ import annotations

from system_modules.llm_engine.intent_router import SystemIntentEntry

WEATHER_INTENTS: list[SystemIntentEntry] = [
    # ── Current weather (priority=5) ──────────────────────────────────
    SystemIntentEntry(
        module="weather-service",
        intent="weather.current",
        priority=5,
        description="Get current weather conditions",
        patterns={
            "uk": [
                r"яка\s+(?:зараз\s+)?погода",
                r"що\s+(?:з|із)\s+погодою",
                r"як\s+(?:там\s+)?(?:на\s+)?(?:вулиці|надворі|дворі)",
                r"розкажи\s+(?:про\s+)?погоду",
            ],
            "en": [
                r"what(?:'s|\s+is)\s+the\s+weather",
                r"(?:current|today(?:'s)?)\s+weather",
                r"how(?:'s|\s+is)\s+(?:the\s+)?weather",
                r"weather\s+(?:right\s+)?now",
            ],
        },
    ),

    # ── Forecast (priority=5) ─────────────────────────────────────────
    SystemIntentEntry(
        module="weather-service",
        intent="weather.forecast",
        priority=5,
        description="Get weather forecast for upcoming days",
        patterns={
            "uk": [
                r"прогноз\s+(?:погоди)?(?:\s+на\s+(?P<period>завтра|тиждень|кілька\s+днів))?",
                r"яка\s+погода\s+(?:буде\s+)?(?P<period>завтра)",
                r"що\s+(?:обіцяють|прогнозують)",
            ],
            "en": [
                r"weather\s+forecast",
                r"forecast(?:\s+for\s+(?P<period>tomorrow|week|next\s+days?))?",
                r"what(?:'s|\s+is)\s+the\s+weather\s+(?P<period>tomorrow)",
                r"weather\s+(?P<period>tomorrow)",
            ],
        },
    ),

    # ── Temperature (priority=5) ──────────────────────────────────────
    SystemIntentEntry(
        module="weather-service",
        intent="weather.temperature",
        priority=5,
        description="Get current temperature",
        patterns={
            "uk": [
                r"(?:скільки|яка)\s+(?:зараз\s+)?(?:градусів|температура)",
                r"температура\s+(?:на\s+)?(?:вулиці|надворі|зараз)",
            ],
            "en": [
                r"what(?:'s|\s+is)\s+the\s+temperature",
                r"(?:current\s+)?temperature",
                r"how\s+(?:hot|cold|warm)\s+is\s+it",
            ],
        },
    ),
]
