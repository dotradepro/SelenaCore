"""
system_modules/llm_engine/structure_extractor.py — Extract verb/noun_class/entity/location from text.

Lightweight keyword lookup using IntentCompiler vocabulary YAML.
Used by SmartMatcher (Tier 1.7) to pre-filter candidates before cosine similarity.
No LLM calls — pure regex/substring matching.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Verb patterns (all languages in one place) ───────────────────────────
# Adding a language = append patterns to the regex string.

_VERBS: dict[str, str] = {
    "ON": (
        r"\bturn on\b|\bswitch on\b|\benable\b|\bactivate\b"
        r"|\bувімкни\b|\bвімкни\b|\bвключи\b|\bзапусти\b|\bактивуй\b"
        r"|увімк\w+"
    ),
    "OFF": (
        r"\bturn off\b|\bswitch off\b|\bdisable\b|\bshut off\b"
        r"|\bвимкни\b|\bвідключи\b"
        r"|вимк\w+"
    ),
    "PLAY": (
        r"\bplay\b|\bput on\b|\blaunch\b"
        r"|\bпостав\b|\bграй\b"
    ),
    "STOP": (
        r"\bstop\b|\bhalt\b|\bend\b|\bquit\b"
        r"|\bстоп\b|\bзупини\b|\bдосить\b"
    ),
    "PAUSE": (
        r"\bpause\b|\bhold\b|\bfreeze\b"
        r"|\bпауза\b|\bпризупини\b"
    ),
    "RESUME": (
        r"\bresume\b|\bcontinue\b"
        r"|\bпродовж\w*"
    ),
    "SET": (
        r"\bset\b|\badjust\b|\bchange\b"
        r"|\bпостав\b|\bвстанови\b|\bналаштуй\b"
    ),
    "QUERY": (
        r"\bwhat\b|\bhow\b|\bshow\b|\bstatus\b|\btell\b"
        r"|\bякий\b|\bяка\b|\bяке\b|\bскільки\b|\bпокажи\b|\bщо\b|\bрозкажи\b|\bхто\b"
    ),
    "SEARCH": (
        r"\bfind\b|\bsearch\b|\blook for\b"
        r"|\bзнайди\b|\bпошукай\b"
    ),
    "SCAN": (
        r"\bscan\b|\bcheck\b"
        r"|\bскануй\b|\bперевір\b"
    ),
    "LIST": (
        r"\blist\b|\bshow all\b"
        r"|\bсписок\b|\bякі\b"
    ),
}

# ── Noun class → entity patterns ────────────────────────────────────────

_NOUN_CLASSES: dict[str, dict[str, str]] = {
    "MEDIA": {
        "radio":  r"\bradio\b|\bmusic\b|\baudio\b|\bрадіо\b|\bмузик\w*",
        "volume": r"\bvolume\b|\bsound\b|\bгучніст\w*|\bзвук\b|\blouder\b|\bquieter\b|\bтихіше\b|\bгучніше\b",
        "track":  r"\btrack\b|\bsong\b|\bтрек\b|\bпісн\w*",
    },
    "DEVICE": {
        "light":  r"\blight\w*\b|\blamp\b|\bbulb\b|\bсвітло\b|\bламп\w*|\bосвітлення\b",
        "device": r"\bdevice\w*\b|\bпристро\w*|\bдевайс\w*",
    },
    "WEATHER": {
        "weather":     r"\bweather\b|\bforecast\b|\bпогод\w*|\bпрогноз\w*|\bнадворі\b|\bдворі\b|\bвулиці\b",
        "temperature": r"\btemperatur\w*\b|\bdegree\w*\b|\bтемператур\w*|\bградус\w*",
    },
    "ENERGY": {
        "power": r"\bpower\b|\bconsumption\b|\benergy\b|\belectricity\b|\bспожив\w*|\bелектрик\w*|\bенерг\w*",
    },
    "PRESENCE": {
        "presence": r"\bpresence\b|\bhome\b|\bhere\b|\baround\b|\bприсутніст\w*|\bвдома\b",
    },
    "AUTOMATION": {
        "automation": r"\bautomation\w*\b|\brule\w*\b|\bавтоматизац\w*",
    },
    "WATCHDOG": {
        "device": r"\bdevice\w*\b|\bпристро\w*",
    },
}

# ── Location patterns ────────────────────────────────────────────────────

_LOCATIONS: dict[str, str] = {
    "kitchen":     r"\bkitchen\b|\bкухн\w*",
    "bedroom":     r"\bbedroom\b|\bспальн\w*",
    "living_room": r"\bliving room\b|\blounge\b|\bвітальн\w*|\bзал\w*|\bгостин\w*",
    "bathroom":    r"\bbathroom\b|\btoilet\b|\bванн\w*|\bтуалет\w*",
    "garage":      r"\bgarage\b|\bгараж\w*",
    "hallway":     r"\bhallway\b|\bcorridor\b|\bкоридор\w*|\bпередпок\w*",
}

# Pre-compile all patterns
_COMPILED_VERBS = {v: re.compile(p, re.IGNORECASE) for v, p in _VERBS.items()}
_COMPILED_NOUNS: dict[str, dict[str, re.Pattern]] = {
    nc: {ent: re.compile(p, re.IGNORECASE) for ent, p in entities.items()}
    for nc, entities in _NOUN_CLASSES.items()
}
_COMPILED_LOCATIONS = {loc: re.compile(p, re.IGNORECASE) for loc, p in _LOCATIONS.items()}


def extract_structure(text: str) -> dict[str, Any]:
    """Extract semantic structure from text via keyword lookup.

    Returns:
        {
            "verb": "ON" | "OFF" | "PLAY" | ... | "UNKNOWN",
            "noun_class": "MEDIA" | "DEVICE" | ... | "UNKNOWN",
            "entity": "radio" | "light" | ... | None,
            "location": "kitchen" | ... | None,
        }
    """
    t = text.lower().strip()

    # Detect verb
    verb = "UNKNOWN"
    for v, pattern in _COMPILED_VERBS.items():
        if pattern.search(t):
            verb = v
            break

    # Detect noun_class + entity
    noun_class = "UNKNOWN"
    entity = None
    for nc, entities in _COMPILED_NOUNS.items():
        for ent, pattern in entities.items():
            if pattern.search(t):
                noun_class = nc
                entity = ent
                break
        if noun_class != "UNKNOWN":
            break

    # Detect location
    location = None
    for loc, pattern in _COMPILED_LOCATIONS.items():
        if pattern.search(t):
            location = loc
            break

    return {
        "verb": verb,
        "noun_class": noun_class,
        "entity": entity,
        "location": location,
    }
