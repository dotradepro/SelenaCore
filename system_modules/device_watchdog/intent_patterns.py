"""
system_modules/device_watchdog/intent_patterns.py — Voice intent patterns for device-watchdog.

Registered with IntentRouter on module startup (Tier 1.5: system module intents).
"""
from __future__ import annotations

from system_modules.llm_engine.intent_router import SystemIntentEntry

WATCHDOG_INTENTS: list[SystemIntentEntry] = [
    SystemIntentEntry(
        module="device-watchdog",
        intent="watchdog.status",
        priority=5,
        description="Report device online/offline status",
        patterns={
            "uk": [
                r"статус\s+пристроїв",
                r"чи\s+всі\s+пристрої\s+працюють",
            ],
            "en": [
                r"device\s+status",
                r"are\s+devices\s+online",
            ],
        },
    ),
    SystemIntentEntry(
        module="device-watchdog",
        intent="watchdog.scan",
        priority=5,
        description="Trigger a device availability scan",
        patterns={
            "uk": [
                r"перевір\s+пристрої",
                r"скануй\s+пристрої",
            ],
            "en": [
                r"scan\s+devices",
                r"check\s+devices",
            ],
        },
    ),
]
