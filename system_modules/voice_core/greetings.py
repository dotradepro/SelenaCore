"""
system_modules/voice_core/greetings.py — Startup greeting picker.

Replaces the old LLM-based greeting that added 500-1000 ms to every boot.
``config/greetings/en.json`` holds time-of-day + gender variants **in
English only**. The TTS-language conversion is done by OutputTranslator
right before Piper speaks — this module never emits non-English text and
never stores translations in the JSON (the rest of Selena follows the
same rule: core is English, translator handles the rest).

Time-of-day buckets follow ``system.timezone`` from ``core.yaml`` (falls
back to container-local time if unset).
"""
from __future__ import annotations

import json
import logging
import os
import random
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GREETINGS_PATH = Path(
    os.environ.get(
        "SELENA_GREETINGS_PATH",
        "/opt/selena-core/config/greetings/en.json",
    )
)


def _time_bucket(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def _system_tz():
    """Resolve tz from core.yaml ``system.timezone`` or local."""
    try:
        from core.config_writer import read_config
        tz_name = (read_config().get("system", {}) or {}).get("timezone", "")
        if tz_name:
            try:
                from zoneinfo import ZoneInfo
                return ZoneInfo(tz_name)
            except Exception:
                pass
    except Exception:
        pass
    return None


def _load() -> dict[str, Any]:
    try:
        return json.loads(_GREETINGS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Failed to load greetings %s: %s", _GREETINGS_PATH, exc)
        return {}


def pick_greeting(name: str, gender: str = "neutral") -> str:
    """Return one English greeting phrase for the given context.

    The caller is responsible for translating the result to the TTS
    language via OutputTranslator — this function never returns anything
    but English text.

    Args:
        name:   Assistant name (already in English / transliterated).
        gender: "female" | "male" | "neutral"

    Falls back gracefully: missing bucket → neutral, missing gender →
    neutral, empty catalogue → plain "<name> ready".
    """
    data = _load()
    if not data:
        return f"{name} ready" if name else "System ready"

    now = datetime.now(tz=_system_tz())
    bucket = _time_bucket(now.hour)
    section = data.get(bucket) or data.get("afternoon") or {}
    variants = section.get(gender) or section.get("neutral") or []

    if not variants:
        return f"{name} ready" if name else "System ready"

    phrase = random.choice(variants)
    return phrase.replace("{name}", name) if name else phrase
