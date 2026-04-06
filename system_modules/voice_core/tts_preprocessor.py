"""
system_modules/voice_core/tts_preprocessor.py — Text preprocessing for Piper TTS

Two transformations:
  1. Lowercase — Piper VITS models produce garbled audio on CAPS
  2. Numbers → words — "23" → "двадцять три" (via num2words)

Language segmentation:
  split_by_language() splits mixed-language text into segments,
  each synthesized by the appropriate PiperVoice (primary or fallback).
  Replaces the old transliterate approach — "WiFi" is now spoken by
  the EN voice, not transliterated to "вайфай".

Usage:
    from system_modules.voice_core.tts_preprocessor import preprocess_for_tts, split_by_language

    clean = preprocess_for_tts("Температура 23 градуси", lang="uk")
    # → "температура двадцять три градуси"

    segments = split_by_language("Вмикаю WiFi. Signal good. Температура 23.")
    # → [("вмикаю", "uk"), ("wifi. signal good.", "en"), ("температура 23.", "uk")]
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# num2words language mapping (ISO 639-1 → num2words code)
_NUM2WORDS_LANGS = {
    "uk": "uk", "en": "en", "de": "de", "fr": "fr", "es": "es",
    "pl": "pl", "ru": "ru", "it": "it", "pt": "pt", "nl": "nl",
    "cs": "cs", "tr": "tr", "ar": "ar", "ja": "ja",
}


@dataclass
class TextSegment:
    """A segment of text with its detected language."""
    text: str
    lang: str  # "en" for Latin, primary_lang for Cyrillic/other


def _numbers_to_words(text: str, lang: str) -> str:
    """Replace all numbers in text with words using num2words."""
    try:
        from num2words import num2words as n2w
    except ImportError:
        return text

    n2w_lang = _NUM2WORDS_LANGS.get(lang, "en")

    def _replace(m: re.Match) -> str:
        s = m.group(0)
        try:
            if "." in s or "," in s:
                val = float(s.replace(",", "."))
                return n2w(val, lang=n2w_lang)
            return n2w(int(s), lang=n2w_lang)
        except Exception:
            return s

    return re.sub(r"\d+[.,]\d+|\d+", _replace, text)


def preprocess_for_tts(text: str, lang: str) -> str:
    """Full TTS preprocessing pipeline: lowercase → numbers.

    Args:
        text: Raw text to preprocess for TTS synthesis
        lang: TTS language code (e.g. "uk", "en", "de")

    Returns:
        Preprocessed text safe for Piper TTS synthesis
    """
    if not text:
        return text

    # 1. Lowercase (Piper VITS bug: CAPS → garbled audio)
    result = text.lower()

    # 2. Numbers → words
    result = _numbers_to_words(result, lang)

    return result.strip()


# ── Language segmentation ────────────────────────────────────────────────

# Regex: detect runs of Latin-only text (words, abbreviations, punctuation between them)
_LATIN_RUN_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9.'\-]*(?:\s+[A-Za-z][A-Za-z0-9.'\-]*)*"
    r"(?:\s*[.!?,;:]\s*(?=[A-Za-z]))?)+",
    re.UNICODE,
)

# Characters that are clearly Cyrillic
_CYRILLIC_RE = re.compile(r"[А-Яа-яІіЇїЄєҐґЁёЎўЪъЫы]")

# Languages with Cyrillic script
_CYRILLIC_LANGS = {"uk", "ru", "bg", "mk", "mn", "sr"}


def split_by_language(text: str, primary_lang: str = "uk") -> list[TextSegment]:
    """Split mixed-language text into segments for multi-voice TTS.

    Latin text segments → lang="en" (spoken by fallback EN voice)
    Primary language segments → lang=primary_lang (spoken by primary voice)

    Numbers in primary-lang segments are converted to words in that language.
    Numbers in EN segments are converted to words in English.

    Example:
        split_by_language("Вмикаю WiFi. Signal good. Температура 23 градуси.", "uk")
        → [
            TextSegment(text="вмикаю", lang="uk"),
            TextSegment(text="wifi. signal good.", lang="en"),
            TextSegment(text="температура двадцять три градуси.", lang="uk"),
          ]
    """
    if not text or not text.strip():
        return []

    # For non-Cyrillic primary languages, no splitting needed
    if primary_lang not in _CYRILLIC_LANGS:
        processed = preprocess_for_tts(text, primary_lang)
        return [TextSegment(text=processed, lang=primary_lang)] if processed else []

    text_lower = text.lower()
    segments: list[TextSegment] = []
    last_end = 0

    for m in _LATIN_RUN_RE.finditer(text_lower):
        start, end = m.start(), m.end()
        latin_text = m.group(0).strip()

        # Skip very short Latin (single letters, common in mixed text)
        # but keep abbreviations (2+ chars) and multi-word runs
        if len(latin_text) <= 1:
            continue

        # Check if there's truly no Cyrillic in this run
        if _CYRILLIC_RE.search(latin_text):
            continue

        # Add preceding primary-language segment
        if start > last_end:
            primary_text = text_lower[last_end:start].strip()
            if primary_text:
                primary_text = _numbers_to_words(primary_text, primary_lang)
                segments.append(TextSegment(text=primary_text, lang=primary_lang))

        # Add Latin segment (EN)
        latin_text = _numbers_to_words(latin_text, "en")
        segments.append(TextSegment(text=latin_text, lang="en"))
        last_end = end

    # Add trailing primary-language segment
    if last_end < len(text_lower):
        trailing = text_lower[last_end:].strip()
        if trailing:
            trailing = _numbers_to_words(trailing, primary_lang)
            segments.append(TextSegment(text=trailing, lang=primary_lang))

    # If no segments were created (pure primary language), return single segment
    if not segments:
        processed = preprocess_for_tts(text, primary_lang)
        return [TextSegment(text=processed, lang=primary_lang)] if processed else []

    return segments
