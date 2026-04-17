"""
core/translation/helsinki_translator.py

Helsinki-NLP / opus-mt drop-in replacement for the Argos-based
``InputTranslator`` / ``OutputTranslator`` in
:mod:`core.translation.local_translator`.

Same public surface — ``is_available()``, ``to_english()``,
``keywords_to_english()``, ``from_english()`` — so the 6 callsites in
voice_core/api/setup do not change. Engine selection lives in
``translation.engine`` (``argos`` | ``helsinki``); the dispatcher in
``local_translator.get_input_translator()`` / ``get_output_translator()``
returns the right backend at call time.

Runtime
-------
Uses CTranslate2 + sentencepiece, both already installed in the
container as transitive deps of ``argostranslate>=1.9.0``. No PyTorch.
Models are pre-converted on Colab via ``ct2-transformers-converter``
and dropped on disk under
``translation.input_model_dir`` / ``translation.output_model_dir``.
See ``docs/helsinki-translator.md`` for the conversion snippet and
required folder layout (model.bin + config.json + source.spm +
target.spm).

Lazy loading
------------
``_ensure_ctranslate2()`` mirrors ``_ensure_argos()`` from
local_translator: nothing is imported until the first ``to_english()``
call. Per-language ``ctranslate2.Translator`` instances are cached on
the singleton in ``self._models[lang]`` and only freed by
``reload_helsinki_translators()`` (called from
``local_translator.reload_translators()`` and the activate routes).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.config_writer import get_nested

logger = logging.getLogger(__name__)

_DEFAULT_INPUT_DIR = "/var/lib/selena/models/translate/helsinki/in"
_DEFAULT_OUTPUT_DIR = "/var/lib/selena/models/translate/helsinki/out"

# Output-side language tokens for multi-target opus-mt models.
#
# The Tatoeba Challenge "tc-big-en-zle" model (recommended replacement
# for the buggy single-pair opus-mt-en-uk, which has a known issue
# generating Russian text instead of Ukrainian) is multilingual on the
# *target* side: a single model produces Belarusian, Russian, or
# Ukrainian depending on a leading language token in the source text.
# Without the token the model defaults to the highest-resource Slavic
# language in its training set, which is Russian — exactly the bug.
#
# The token MUST be the very first thing in the source string, before
# any other text or whitespace. opus-mt language tokens use ISO 639-3
# wrapped in `>>...<<` brackets — `>>ukr<<` for Ukrainian, `>>rus<<`
# for Russian, `>>bel<<` for Belarusian.
#
# If you replace tc-big-en-zle with a dedicated single-pair model
# (e.g. opus-mt-en-fr) the prefix becomes unnecessary — that model
# will treat `>>fra<<` as junk text and translate it literally. In
# that case set this dict's value to "" for that language. Future
# improvement: read a `language_token` field from a metadata.json
# inside each model dir so the wrapper can auto-detect.
_OUTPUT_LANG_TOKENS: dict[str, str] = {
    "uk": ">>ukr<<",
    # Add more entries here when adding new tc-big-en-zle / similar
    # multi-target pairs.
}

_ct2_loaded = False


def _ensure_ctranslate2() -> bool:
    """Lazy-load ctranslate2 + sentencepiece on first use.

    Both packages ship with ``argostranslate`` so they should always be
    importable in this container. We still wrap the import to mirror the
    Argos pattern and degrade gracefully on stripped-down builds.
    """
    global _ct2_loaded
    if _ct2_loaded:
        return True
    try:
        import ctranslate2  # noqa: F401
        import sentencepiece  # noqa: F401
        _ct2_loaded = True
        return True
    except ImportError as exc:
        logger.warning(
            "ctranslate2/sentencepiece not installed (%s) — Helsinki engine "
            "unavailable, falling back to pass-through.",
            exc,
        )
        return False


_cuda_available: bool | None = None


def _cuda_device_available() -> bool:
    """Probe once whether ctranslate2 sees a CUDA device.

    ``int8_float16`` is the recommended compute_type for int8-quantized
    models on CUDA — weights stay int8, math runs in fp16. ~3-4x faster
    than CPU int8 on discrete NVIDIA. Benchmarked against the
    opus-mt-tc-big-* models shipped via ct2-transformers-converter.
    """
    global _cuda_available
    if _cuda_available is not None:
        return _cuda_available
    try:
        import ctranslate2
        _cuda_available = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        _cuda_available = False
    if _cuda_available:
        logger.info("Helsinki: CUDA device detected — translators will load on GPU")
    return _cuda_available


def _layout_ok(d: Path) -> bool:
    """Check that a model directory has every file CT2 + spm need."""
    return (
        (d / "model.bin").is_file()
        and (d / "source.spm").is_file()
        and (d / "target.spm").is_file()
    )


class _BaseHelsinki:
    """Shared loading + caching logic for input/output translators."""

    def __init__(self, models_dir: Path) -> None:
        self._models_dir = models_dir
        # pair_key (e.g. "uk-en" / "en-uk") → (translator, src_sp, tgt_sp)
        self._models: dict[str, tuple[Any, Any, Any]] = {}

    def _list_pairs(self) -> list[Path]:
        if not self._models_dir.is_dir():
            return []
        return [d for d in self._models_dir.iterdir() if d.is_dir() and _layout_ok(d)]

    def is_available(self) -> bool:
        return bool(self._list_pairs())

    def _load(self, pair_key: str) -> tuple[Any, Any, Any]:
        if pair_key in self._models:
            return self._models[pair_key]
        if not _ensure_ctranslate2():
            raise FileNotFoundError("ctranslate2 not importable")
        d = self._models_dir / pair_key
        if not _layout_ok(d):
            raise FileNotFoundError(
                f"Helsinki model not found or incomplete: {d} "
                "(expected model.bin + source.spm + target.spm)"
            )
        import ctranslate2
        import sentencepiece as spm

        if _cuda_device_available():
            translator = ctranslate2.Translator(
                str(d),
                device="cuda",
                compute_type="int8_float16",
            )
        else:
            translator = ctranslate2.Translator(
                str(d),
                device="cpu",
                compute_type="int8",
                inter_threads=1,
                intra_threads=2,
            )
        src_sp = spm.SentencePieceProcessor()
        src_sp.Load(str(d / "source.spm"))
        tgt_sp = spm.SentencePieceProcessor()
        tgt_sp.Load(str(d / "target.spm"))
        self._models[pair_key] = (translator, src_sp, tgt_sp)
        logger.info("Helsinki: loaded %s from %s", pair_key, d)
        return self._models[pair_key]

    def _translate(
        self,
        text: str,
        pair_key: str,
        *,
        lang_prefix: str = "",
        max_decoding_length: int = 256,
    ) -> str:
        try:
            translator, src_sp, tgt_sp = self._load(pair_key)
        except FileNotFoundError as exc:
            logger.warning(
                "Helsinki: model missing for %s (%s) — falling back to "
                "pass-through. Drop the converted CT2 folder under %s.",
                pair_key, exc, self._models_dir,
            )
            return text

        # Sentencepiece encoding of the source text. The language
        # token (e.g. ">>ukr<<" for tc-big-en-zle) MUST NOT go through
        # sentencepiece — it's a special token that lives in the
        # model's full vocab (shared_vocabulary.json) but not in the
        # spm vocab. Encoding it as raw text yields <unk> pieces. The
        # correct path is to encode the actual sentence first, then
        # PREPEND the language token as its own piece. CTranslate2
        # looks up every input piece against shared_vocabulary.json
        # so the special token resolves to its real ID at translate
        # time.
        tokens = src_sp.encode(text, out_type=str)
        if lang_prefix:
            tokens = [lang_prefix] + tokens

        # Marian Helsinki models expect "</s>" at the end of source
        # tokens. CTranslate2's add_source_eos parameter is read from
        # config.json — for tc-big models it's false (verified on
        # opus-mt-tc-big-{zle-en,en-zle}), so the caller is
        # responsible for appending </s> manually. Without it the
        # decoder produces multi-sentence run-ons and beam-search
        # garbage like "weather weather .. what weather outdoor...".
        tokens.append("</s>")

        # Anti-repetition + length cap. opus-mt has a well-known
        # decoder pathology on short voice utterances: with beam
        # search alone it loops on the last n-gram. Combination
        # below was tuned against trace-bench on qwen2.5:1.5b /
        # 40-case corpus.
        cap = max(32, min(max_decoding_length, len(tokens) * 3))
        results = translator.translate_batch(
            [tokens],
            beam_size=4,
            length_penalty=1.0,
            no_repeat_ngram_size=3,
            repetition_penalty=1.3,
            max_decoding_length=cap,
        )
        out_tokens = results[0].hypotheses[0]
        # Strip trailing </s> if the decoder emitted it.
        if out_tokens and out_tokens[-1] == "</s>":
            out_tokens = out_tokens[:-1]
        return tgt_sp.decode(out_tokens)

    def reset(self) -> None:
        """Drop every loaded ctranslate2.Translator instance."""
        self._models.clear()


class HelsinkiInputTranslator(_BaseHelsinki):
    """Any language → English. Drop-in for ``InputTranslator``."""

    def __init__(self) -> None:
        super().__init__(Path(get_nested(
            "translation.input_model_dir", _DEFAULT_INPUT_DIR,
        )))

    def to_english(self, text: str, source_lang: str) -> str:
        if not text or not text.strip():
            return text
        if source_lang == "en":
            return text
        if all(ord(c) < 128 for c in text):
            # Pure-ASCII input — likely already English, mirrors Argos behaviour.
            return text

        from core.translation.local_translator import _normalize_for_mt
        prepared = _normalize_for_mt(text)
        if prepared != text:
            logger.debug(
                "IN [helsinki/%s] normalised: %r → %r",
                source_lang, text[:80], prepared[:80],
            )

        try:
            result = self._translate(prepared, f"{source_lang}-en")
            if result:
                logger.debug(
                    "IN [helsinki/%s] '%s' → '%s'",
                    source_lang, prepared[:60], result[:60],
                )
                return result
        except Exception as exc:
            logger.warning("HelsinkiInputTranslator error: %s", exc)
        return prepared

    def keywords_to_english(
        self, keywords: list[str], source_lang: str,
    ) -> list[str]:
        if source_lang == "en":
            return keywords
        if all(all(ord(c) < 128 for c in k) for k in keywords):
            return keywords
        return [self.to_english(kw, source_lang) for kw in keywords]


class HelsinkiOutputTranslator(_BaseHelsinki):
    """English → target language. Drop-in for ``OutputTranslator``."""

    def __init__(self) -> None:
        super().__init__(Path(get_nested(
            "translation.output_model_dir", _DEFAULT_OUTPUT_DIR,
        )))

    def from_english(self, text: str, target_lang: str) -> str:
        if not text or not text.strip():
            return text
        if target_lang == "en":
            return text
        # Language-agnostic guard: input must be predominantly Latin
        # letters to be treated as English. Mirrors the Argos backend.
        latin = sum(1 for c in text if c.isalpha() and "a" <= c.lower() <= "z")
        other = sum(1 for c in text if c.isalpha()) - latin
        if other > latin:
            return text

        # Multi-target tc-big-en-zle (and friends) need a language
        # token piece prepended to the encoded source. Single-pair
        # models like opus-mt-en-fr should leave this empty so the
        # decoder isn't fed an unknown token. The mapping lives at
        # the top of this module.
        lang_prefix = _OUTPUT_LANG_TOKENS.get(target_lang, "")

        try:
            result = self._translate(
                text, f"en-{target_lang}", lang_prefix=lang_prefix,
            )
            if result:
                logger.debug(
                    "OUT [helsinki/%s] '%s' → '%s'",
                    target_lang, text[:60], result[:60],
                )
                return result
        except Exception as exc:
            logger.warning("HelsinkiOutputTranslator error: %s", exc)
        return text


# ── Singletons ──────────────────────────────────────────────────────

_helsinki_input: HelsinkiInputTranslator | None = None
_helsinki_output: HelsinkiOutputTranslator | None = None


def get_helsinki_input() -> HelsinkiInputTranslator:
    global _helsinki_input
    if _helsinki_input is None:
        _helsinki_input = HelsinkiInputTranslator()
    return _helsinki_input


def get_helsinki_output() -> HelsinkiOutputTranslator:
    global _helsinki_output
    if _helsinki_output is None:
        _helsinki_output = HelsinkiOutputTranslator()
    return _helsinki_output


def reload_helsinki_translators() -> None:
    """Drop singletons + every loaded CT2 model.

    Called by ``local_translator.reload_translators()`` after engine
    swap or model install/delete so the next request reloads from
    disk.
    """
    global _helsinki_input, _helsinki_output
    if _helsinki_input is not None:
        _helsinki_input.reset()
    if _helsinki_output is not None:
        _helsinki_output.reset()
    _helsinki_input = None
    _helsinki_output = None
