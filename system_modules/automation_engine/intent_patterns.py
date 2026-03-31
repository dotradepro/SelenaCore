"""
system_modules/automation_engine/intent_patterns.py — Voice intent patterns for automation-engine.

Registered with IntentRouter on module startup (Tier 1.5: system module intents).
"""
from __future__ import annotations

from system_modules.llm_engine.intent_router import SystemIntentEntry

AUTOMATION_INTENTS: list[SystemIntentEntry] = [
    SystemIntentEntry(
        module="automation-engine",
        intent="automation.list",
        priority=5,
        description="List all automation rules",
        patterns={
            "uk": [
                r"які\s+автоматизації",
                r"список\s+автоматизацій",
            ],
            "en": [
                r"list\s+automations",
                r"show\s+automations",
            ],
        },
    ),
    SystemIntentEntry(
        module="automation-engine",
        intent="automation.enable",
        priority=5,
        description="Enable an automation rule by name",
        patterns={
            "uk": [
                r"увімкни\s+автоматизацію\s+(?P<name>.+)",
            ],
            "en": [
                r"enable\s+automation\s+(?P<name>.+)",
            ],
        },
    ),
    SystemIntentEntry(
        module="automation-engine",
        intent="automation.disable",
        priority=5,
        description="Disable an automation rule by name",
        patterns={
            "uk": [
                r"вимкни\s+автоматизацію\s+(?P<name>.+)",
            ],
            "en": [
                r"disable\s+automation\s+(?P<name>.+)",
            ],
        },
    ),
    SystemIntentEntry(
        module="automation-engine",
        intent="automation.status",
        priority=5,
        description="Get automation engine status",
        patterns={
            "uk": [
                r"статус\s+автоматизацій",
            ],
            "en": [
                r"automation\s+status",
            ],
        },
    ),
]
