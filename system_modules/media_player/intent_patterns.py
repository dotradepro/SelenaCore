"""
system_modules/media_player/intent_patterns.py — Voice intent patterns for media-player.

Registered with IntentRouter on module startup (Tier 1.5: system module intents).
Patterns use regex with optional named groups for parameter extraction.
"""
from __future__ import annotations

from system_modules.llm_engine.intent_router import SystemIntentEntry

# Higher priority = checked first.
# Specific intents (with params) must have higher priority than generic ones
# to avoid "play rock radio" matching generic "media.play_radio" instead of "media.play_genre".

MEDIA_INTENTS: list[SystemIntentEntry] = [
    # ── Playback with parameters (priority=10) ──────────────────────────

    SystemIntentEntry(
        module="media-player",
        intent="media.volume_set",
        priority=10,
        description="Set volume to specific level",
        patterns={
            "uk": [
                r"гучність\s+(?:на\s+)?(?P<level>\d+)",
                r"постав\s+гучність\s+(?P<level>\d+)",
            ],
            "en": [
                r"(?:set\s+)?volume\s+(?:to\s+)?(?P<level>\d+)",
            ],
        },
    ),
    SystemIntentEntry(
        module="media-player",
        intent="media.play_genre",
        priority=10,
        description="Play radio by genre",
        patterns={
            "uk": [
                r"(?:увімкни|включи|постав)\s+(?P<genre>рок|джаз|класик\w*|ембієнт|ambient|lofi|поп|новини)\s*(?:музику|радіо)?",
            ],
            "en": [
                r"play\s+(?P<genre>rock|jazz|classical|ambient|lofi|pop|news)\s*(?:music|radio)?",
            ],
        },
    ),
    SystemIntentEntry(
        module="media-player",
        intent="media.play_radio_name",
        priority=10,
        description="Play specific radio station by name",
        patterns={
            "uk": [
                r"(?:увімкни|включи)\s+(?:радіо|станцію)\s+(?P<station_name>.+)",
            ],
            "en": [
                r"(?:play|tune)\s+(?:radio\s+)?station\s+(?P<station_name>.+)",
                r"play\s+(?P<station_name>.+)\s+radio",
            ],
        },
    ),
    SystemIntentEntry(
        module="media-player",
        intent="media.play_search",
        priority=10,
        description="Search and play track",
        patterns={
            "uk": [
                r"(?:знайди|пошукай)\s+(?P<query>.+)",
            ],
            "en": [
                r"(?:find|search)\s+(?:for\s+)?(?P<query>.+)",
            ],
        },
    ),

    # ── Simple playback (priority=5) ────────────────────────────────────

    SystemIntentEntry(
        module="media-player",
        intent="media.play_radio",
        priority=5,
        description="Play radio (generic)",
        patterns={
            "uk": [
                r"(?:увімкни|включи|постав)\s+радіо",
                r"(?:увімкни|включи)\s+музику",
            ],
            "en": [
                r"(?:play|turn on)\s+(?:the\s+)?radio",
                r"(?:play|turn on)\s+(?:some\s+)?music",
            ],
        },
    ),

    # ── Transport controls (priority=5) ─────────────────────────────────

    SystemIntentEntry(
        module="media-player",
        intent="media.pause",
        priority=5,
        description="Pause playback",
        patterns={
            "uk": [r"пауза", r"на паузу", r"зупини", r"призупини"],
            "en": [r"\bpause\b", r"hold\s+(?:the\s+)?music"],
        },
    ),
    SystemIntentEntry(
        module="media-player",
        intent="media.resume",
        priority=5,
        description="Resume playback",
        patterns={
            "uk": [r"продовж(?:и|уй)", r"далі грай"],
            "en": [r"\bresume\b", r"continue\s+play"],
        },
    ),
    SystemIntentEntry(
        module="media-player",
        intent="media.stop",
        priority=5,
        description="Stop playback",
        patterns={
            "uk": [r"\bстоп\b", r"зупини\s*музику", r"досить", r"вимкни\s+музику"],
            "en": [r"\bstop\b", r"stop\s+(?:the\s+)?music"],
        },
    ),
    SystemIntentEntry(
        module="media-player",
        intent="media.next",
        priority=5,
        description="Next track",
        patterns={
            "uk": [r"наступн(?:ий|а|е)", r"далі", r"наступний\s+трек"],
            "en": [r"\bnext\b", r"next\s+track", r"skip"],
        },
    ),
    SystemIntentEntry(
        module="media-player",
        intent="media.previous",
        priority=5,
        description="Previous track",
        patterns={
            "uk": [r"попередн(?:ій|я|є)", r"назад", r"попередній\s+трек"],
            "en": [r"\bprevious\b", r"prev(?:ious)?\s+track", r"go\s+back"],
        },
    ),

    # ── Volume (priority=5) ─────────────────────────────────────────────

    SystemIntentEntry(
        module="media-player",
        intent="media.volume_up",
        priority=5,
        description="Increase volume",
        patterns={
            "uk": [r"гучніше", r"погучніше", r"додай\s+гучност"],
            "en": [r"louder", r"volume\s+up", r"turn\s+(?:it\s+)?up"],
        },
    ),
    SystemIntentEntry(
        module="media-player",
        intent="media.volume_down",
        priority=5,
        description="Decrease volume",
        patterns={
            "uk": [r"тихіше", r"потихіше", r"зменш\s+гучність"],
            "en": [r"quieter", r"volume\s+down", r"turn\s+(?:it\s+)?down"],
        },
    ),

    # ── Info / toggles (priority=5) ─────────────────────────────────────

    SystemIntentEntry(
        module="media-player",
        intent="media.whats_playing",
        priority=5,
        description="What is currently playing",
        patterns={
            "uk": [r"що\s+(?:грає|звучить|граєш)", r"яка\s+(?:пісня|музика)"],
            "en": [r"what.s\s+playing", r"what\s+(?:is\s+)?(?:this|playing)"],
        },
    ),
    SystemIntentEntry(
        module="media-player",
        intent="media.shuffle_toggle",
        priority=5,
        description="Toggle shuffle mode",
        patterns={
            "uk": [r"перемішай", r"випадков(?:ий|е)\s+порядок"],
            "en": [r"\bshuffle\b", r"mix\s+(?:it\s+)?up"],
        },
    ),
]
