"""
system_modules/voice_core/wake_acks.py — Wake-word acknowledgement picker.

``config/wake_acks/en.json`` holds a flat list of short English
acknowledgement phrases. One is chosen at random on every wake
detection so the assistant doesn't sound like a parrot. Translation to
the TTS language is done by OutputTranslator right before Piper speaks,
same rule as greetings.py.
"""
from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path

logger = logging.getLogger(__name__)

_ACKS_PATH = Path(
    os.environ.get(
        "SELENA_WAKE_ACKS_PATH",
        "/opt/selena-core/config/wake_acks/en.json",
    )
)

_FALLBACK = "Yes?"


def pick_wake_ack() -> str:
    """Return one English wake-word acknowledgement phrase at random."""
    try:
        data = json.loads(_ACKS_PATH.read_text(encoding="utf-8"))
        phrases = data.get("phrases") or []
        if phrases:
            return random.choice(phrases)
    except Exception as exc:
        logger.debug("Failed to load wake acks %s: %s", _ACKS_PATH, exc)
    return _FALLBACK
