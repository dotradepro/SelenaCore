"""
core/translation/local_translator.py

Local offline translation using Argos Translate.

InputTranslator  — any language → English (after Vosk STT, before IntentRouter)
OutputTranslator — English → target language (after IntentRouter, before Piper TTS)

Models are installed via Settings → Translation tab (Argos packages, ~50-100 MB each).
Fully offline after download.

Language-agnostic quality boost
-------------------------------
NMT models (opus-mt under the Argos hood) were trained on normal written
sentences: capitalised first word, trailing punctuation. Vosk returns
raw lowercase tokens with no punctuation, which measurably degrades
translation quality on every language pair (+3-5 BLEU is a typical gain
just from restoring grammar). ``_normalize_for_mt`` restores that shape
before Argos runs and is **purely structural** — no word lists, no
per-language regex, no hardcoded vocabulary of any sort.

If Argos still produces noise after this (tc-big opus-mt-tc-big-xx-en
models often help), swap the downloaded package via Settings →
Translation. See docs/intent-routing.md for model upgrade guidance.
"""
from __future__ import annotations

import logging

from core.config_writer import get_value

logger = logging.getLogger(__name__)

_argos_loaded = False


def _ensure_argos() -> bool:
    """Lazy-load argostranslate on first use."""
    global _argos_loaded
    if _argos_loaded:
        return True
    try:
        import argostranslate.translate  # noqa: F401
        _argos_loaded = True
        return True
    except ImportError:
        logger.warning("argostranslate not installed")
        return False


def _normalize_for_mt(text: str) -> str:
    """Prep Vosk output for NMT input: capitalise + trailing punctuation.

    Works identically for every language — zero per-language code.
    NMT models were trained on grammatical sentences; giving them Vosk's
    lowercase/no-punct raw output costs 3-5 BLEU across every pair. The
    fix is to capitalise the first letter and add a full stop when none
    is present.
    """
    if not text or not text.strip():
        return text
    s = text.strip()
    if not s:
        return s
    # Capitalise the first character (Unicode-aware via str.upper())
    s = s[0].upper() + s[1:]
    if s[-1] not in ".!?…":
        s += "."
    return s


class InputTranslator:
    """Any language → English.

    Called in two places:
      1. voice_core/_process_command — right after Vosk STT
      2. core/api/helpers.py — translate_to_en / translate_keywords_to_en
    """

    def is_available(self) -> bool:
        if not _ensure_argos():
            return False
        import argostranslate.package
        installed = argostranslate.package.get_installed_packages()
        return any(p.to_code == "en" for p in installed)

    def to_english(self, text: str, source_lang: str) -> str:
        if not text or not text.strip():
            return text
        if source_lang == "en":
            return text
        if all(ord(c) < 128 for c in text):
            return text
        if not _ensure_argos():
            return text

        # Language-agnostic grammar normalisation before Argos.
        prepared = _normalize_for_mt(text)
        if prepared != text:
            logger.debug(
                "IN [%s] normalised: %r → %r",
                source_lang, text[:80], prepared[:80],
            )

        try:
            import argostranslate.translate
            result = argostranslate.translate.translate(
                prepared, source_lang, "en",
            )
            if result:
                logger.debug(
                    "IN [%s] '%s' → '%s'",
                    source_lang, prepared[:60], result[:60],
                )
                return result
        except Exception as exc:
            logger.warning("InputTranslator error: %s", exc)
        return prepared

    def keywords_to_english(self, keywords: list[str], source_lang: str) -> list[str]:
        if source_lang == "en":
            return keywords
        if all(all(ord(c) < 128 for c in k) for k in keywords):
            return keywords
        return [self.to_english(kw, source_lang) for kw in keywords]


class OutputTranslator:
    """English → target language.

    Called in voice_core/_process_command before every _enqueue_speech.
    """

    def is_available(self) -> bool:
        if not _ensure_argos():
            return False
        import argostranslate.package
        installed = argostranslate.package.get_installed_packages()
        return any(p.from_code == "en" for p in installed)

    def from_english(self, text: str, target_lang: str) -> str:
        if not text or not text.strip():
            return text
        if target_lang == "en":
            return text
        if not _ensure_argos():
            return text
        try:
            import argostranslate.translate
            result = argostranslate.translate.translate(text, "en", target_lang)
            if result:
                logger.debug("OUT [%s] '%s' → '%s'", target_lang, text[:60], result[:60])
                return result
        except Exception as exc:
            logger.warning("OutputTranslator error: %s", exc)
        return text


# ── Singletons ──────────────────────────────────────────────────────

_input: InputTranslator | None = None
_output: OutputTranslator | None = None


def get_input_translator() -> InputTranslator:
    global _input
    if _input is None:
        _input = InputTranslator()
    return _input


def get_output_translator() -> OutputTranslator:
    global _output
    if _output is None:
        _output = OutputTranslator()
    return _output


def reload_translators() -> None:
    global _input, _output
    _input = None
    _output = None
