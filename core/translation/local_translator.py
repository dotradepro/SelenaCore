"""
core/translation/local_translator.py

Local offline translation using Helsinki-NLP opus-mt models via CTranslate2.

InputTranslator  — any language → English (after Vosk STT, before IntentRouter)
OutputTranslator — English → target language (after IntentRouter, before Piper TTS)

Models are downloaded on demand via Settings → Translation (downloader.py).
CTranslate2 int8 quantization keeps RAM usage low (~150 MB per model).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from core.config_writer import get_value

logger = logging.getLogger(__name__)


class _BaseTranslator:
    """Shared CTranslate2 + SentencePiece loader for opus-mt models."""

    def __init__(self, config_key: str, default_dir: str) -> None:
        self._config_key = config_key
        self._default_dir = default_dir
        self._translator = None
        self._sp = None
        self._ready = False

    def _model_dir(self) -> Path:
        return Path(get_value("translation", self._config_key, self._default_dir))

    def is_available(self) -> bool:
        return (self._model_dir() / "model.bin").exists()

    def _load(self) -> bool:
        if self._ready:
            return True
        d = self._model_dir()
        if not (d / "model.bin").exists():
            return False
        try:
            import ctranslate2
            import sentencepiece as spm

            self._translator = ctranslate2.Translator(
                str(d), device="cpu", inter_threads=2,
            )
            sp_path = d / "source.spm"
            if not sp_path.exists():
                # Some conversions place it as sentencepiece.model
                sp_path = d / "sentencepiece.model"
            self._sp = spm.SentencePieceProcessor()
            self._sp.Load(str(sp_path))
            self._ready = True
            logger.info("%s loaded from %s", self.__class__.__name__, d)
            return True
        except Exception as exc:
            logger.warning("%s load failed: %s", self.__class__.__name__, exc)
            return False

    def _translate(self, text: str, lang_tag: str) -> str:
        if not self._load():
            return text
        try:
            tagged = f">>{lang_tag}<< {text}"
            tokens = self._sp.Encode(tagged, out_type=str)
            result = self._translator.translate_batch(
                [tokens], max_decoding_length=256,
            )
            return self._sp.Decode(result[0].hypotheses[0])
        except Exception as exc:
            logger.warning("Translation error: %s", exc)
            return text

    def _translate_batch(self, texts: list[str], lang_tag: str) -> list[str]:
        if not self._load():
            return texts
        try:
            tagged = [f">>{lang_tag}<< {t}" for t in texts]
            batch = [self._sp.Encode(t, out_type=str) for t in tagged]
            results = self._translator.translate_batch(
                batch, max_decoding_length=128,
            )
            return [self._sp.Decode(r.hypotheses[0]) for r in results]
        except Exception as exc:
            logger.warning("Batch translation error: %s", exc)
            return texts

    def reset(self) -> None:
        self._translator = None
        self._sp = None
        self._ready = False


class InputTranslator(_BaseTranslator):
    """Any language → English.

    Called in two places:
      1. voice_core/_process_command — right after Vosk STT
      2. core/api/helpers.py — translate_to_en / translate_keywords_to_en
    """

    def __init__(self) -> None:
        super().__init__(
            "input_model_dir",
            "/var/lib/selena/models/translate/mul-en",
        )

    def to_english(self, text: str, source_lang: str) -> str:
        if not text or not text.strip():
            return text
        if source_lang == "en":
            return text
        # Skip if text is already ASCII (likely English)
        if all(ord(c) < 128 for c in text):
            return text
        result = self._translate(text, source_lang)
        logger.debug("IN [%s] '%s' → '%s'", source_lang, text[:60], result[:60])
        return result

    def keywords_to_english(
        self, keywords: list[str], source_lang: str,
    ) -> list[str]:
        if source_lang == "en":
            return keywords
        if all(all(ord(c) < 128 for c in k) for k in keywords):
            return keywords
        return self._translate_batch(keywords, source_lang)


class OutputTranslator(_BaseTranslator):
    """English → target language.

    Called in voice_core/_process_command before every _enqueue_speech.
    """

    def __init__(self) -> None:
        super().__init__(
            "output_model_dir",
            "/var/lib/selena/models/translate/en-mul",
        )

    def from_english(self, text: str, target_lang: str) -> str:
        if not text or not text.strip():
            return text
        if target_lang == "en":
            return text
        result = self._translate(text, target_lang)
        logger.debug("OUT [%s] '%s' → '%s'", target_lang, text[:60], result[:60])
        return result


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
    if _input is not None:
        _input.reset()
    if _output is not None:
        _output.reset()
    _input = None
    _output = None
