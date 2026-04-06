"""
system_modules/voice_core/tts_preprocessor.py — Text preprocessing for Piper TTS

Two transformations:
  1. Lowercase — Piper VITS models produce garbled audio on CAPS
  2. Numbers → words — "23" → "двадцять три" (via num2words)

Single-voice mode: there is no language splitting. The whole text is
synthesized by the primary voice configured in core.yaml. Mixed-language
phrases (e.g. "Вмикаю WiFi") are spoken by the primary voice — Latin
words may sound off but no fallback voice is used.

Usage:
    from system_modules.voice_core.tts_preprocessor import preprocess_for_tts

    clean = preprocess_for_tts("Температура 23 градуси", lang="uk")
    # → "температура двадцять три градуси"
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# num2words language mapping (ISO 639-1 → num2words code)
_NUM2WORDS_LANGS = {
    "uk": "uk", "en": "en", "de": "de", "fr": "fr", "es": "es",
    "pl": "pl", "ru": "ru", "it": "it", "pt": "pt", "nl": "nl",
    "cs": "cs", "tr": "tr", "ar": "ar", "ja": "ja",
}


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
