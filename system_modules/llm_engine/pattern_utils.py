"""
system_modules/llm_engine/pattern_utils.py — Utility for converting
natural English phrases into regex patterns for intent matching.

Used by PatternGenerator (entity auto-patterns) and IntentRouter (LLM-generated patterns).
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Articles that should be optional in patterns
_OPTIONAL_ARTICLES = {"the", "a", "an"}


def phrase_to_regex(phrase: str) -> str:
    """Convert a natural English phrase to a regex pattern.

    "play hit fm radio" → "play\\s+hit\\s+fm\\s+radio"
    "turn on the light" → "turn\\s+on\\s+(?:the\\s+)?light"

    Rules:
      - Lowercase, strip leading/trailing whitespace
      - Articles (the, a, an) become optional groups
      - Words are joined with \\s+
      - Special regex chars in words are escaped
    """
    phrase = phrase.lower().strip()
    # ASCII-only safety net: pattern fragments coming from LLM must be English.
    # Entity names (radio "ХІТ FM", etc.) never go through this helper —
    # they are escaped via re.escape() inside PatternGenerator, so this guard
    # does not affect legitimate Cyrillic entity names.
    if not phrase.isascii():
        return ""
    # Remove punctuation except hyphens (e.g. "wi-fi")
    phrase = re.sub(r"[^\w\s\-]", "", phrase)
    # Collapse multiple spaces
    phrase = re.sub(r"\s+", " ", phrase).strip()

    if not phrase:
        return ""

    words = phrase.split()
    parts: list[str] = []

    i = 0
    while i < len(words):
        word = words[i]
        if word in _OPTIONAL_ARTICLES and i + 1 < len(words):
            # Make article optional, attach to next word
            next_word = re.escape(words[i + 1])
            parts.append(f"(?:{re.escape(word)}\\s+)?{next_word}")
            i += 2
        else:
            parts.append(re.escape(word))
            i += 1

    return "\\s+".join(parts)


def validate_pattern(pattern: str) -> bool:
    """Check that a regex pattern compiles without error."""
    try:
        re.compile(pattern, re.IGNORECASE)
        return True
    except re.error:
        return False


def deduplicate_pattern(new_pattern: str, existing_patterns: list[str]) -> bool:
    """Check if new_pattern is functionally duplicate of any existing pattern.

    Returns True if the new pattern is a duplicate (should be skipped).
    """
    # Normalize for comparison: remove optional groups, collapse whitespace markers
    def _normalize(p: str) -> str:
        p = p.lower()
        p = re.sub(r"\(\?:[^)]*\)", "", p)  # remove optional groups
        p = re.sub(r"\\s\+", " ", p)  # \\s+ → space
        p = re.sub(r"\s+", " ", p).strip()
        return p

    norm_new = _normalize(new_pattern)
    if not norm_new:
        return True

    for existing in existing_patterns:
        if _normalize(existing) == norm_new:
            return True

    return False
