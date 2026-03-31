"""
system_modules/presence_detection/intent_patterns.py — Voice intent patterns for presence-detection.

Registered with IntentRouter on module startup (Tier 1.5: system module intents).
Patterns use regex with optional named groups for parameter extraction.
"""
from __future__ import annotations

from system_modules.llm_engine.intent_router import SystemIntentEntry

# Higher priority = checked first.
# Specific intents (with params) must have higher priority than generic ones.

PRESENCE_INTENTS: list[SystemIntentEntry] = [
    # ── Parameterised (priority=10) ───────────────────────────────────────

    SystemIntentEntry(
        module="presence-detection",
        intent="presence.check_user",
        priority=10,
        description="Check if a specific user is home",
        patterns={
            "uk": [
                r"чи\s+є\s+(?P<name>.+)\s+вдома",
                r"(?P<name>.+)\s+вдома\?",
            ],
            "en": [
                r"is\s+(?P<name>.+)\s+(?:at\s+)?home",
                r"is\s+(?P<name>.+)\s+(?:here|around)",
            ],
        },
    ),

    # ── Simple queries (priority=5) ───────────────────────────────────────

    SystemIntentEntry(
        module="presence-detection",
        intent="presence.who_home",
        priority=5,
        description="List all users currently at home",
        patterns={
            "uk": [
                r"хто\s+вдома",
                r"хто\s+є\s+вдома",
                r"хто\s+зараз\s+вдома",
            ],
            "en": [
                r"who.s\s+home",
                r"who\s+is\s+home",
                r"who\s+is\s+at\s+home",
                r"who.s\s+(?:here|around)",
            ],
        },
    ),
    SystemIntentEntry(
        module="presence-detection",
        intent="presence.status",
        priority=5,
        description="Presence status summary (home / away counts)",
        patterns={
            "uk": [
                r"статус\s+присутності",
                r"присутність",
            ],
            "en": [
                r"presence\s+status",
                r"presence\s+summary",
            ],
        },
    ),
]
