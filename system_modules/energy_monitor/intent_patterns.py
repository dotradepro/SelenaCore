"""
system_modules/energy_monitor/intent_patterns.py — Voice intent patterns for energy-monitor.

Registered with IntentRouter on module startup (Tier 1.5: system module intents).
"""
from __future__ import annotations

from system_modules.llm_engine.intent_router import SystemIntentEntry

ENERGY_INTENTS: list[SystemIntentEntry] = [
    SystemIntentEntry(
        module="energy-monitor",
        intent="energy.current",
        priority=5,
        description="Get current power consumption",
        patterns={
            "uk": [
                r"яке\s+споживання",
                r"скільки\s+електрики",
            ],
            "en": [
                r"power\s+consumption",
                r"how\s+much\s+power",
                r"current\s+power",
            ],
        },
    ),
    SystemIntentEntry(
        module="energy-monitor",
        intent="energy.today",
        priority=5,
        description="Get today's energy consumption",
        patterns={
            "uk": [
                r"скільки\s+електрики\s+за\s+сьогодні",
                r"споживання\s+за\s+сьогодні",
            ],
            "en": [
                r"energy\s+today",
                r"today.s\s+consumption",
                r"today.s\s+energy",
            ],
        },
    ),
]
