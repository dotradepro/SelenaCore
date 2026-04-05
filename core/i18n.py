"""
core/i18n.py — Lightweight i18n for Python backend.

Usage:
    from core.i18n import t, get_system_lang

    # Simple translation
    text = t("media.paused")

    # With interpolation
    text = t("media.volume_set", level=50)

    # Explicit language
    text = t("media.paused", lang="uk")

Locale files: config/locales/{lang}.json (flat or nested JSON).
Fallback chain: requested lang → "en" → raw key (never crashes).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCALES_DIR = Path(__file__).resolve().parent.parent / "config" / "locales"

_cache: dict[str, dict[str, str]] = {}
_lock = threading.Lock()

_lang_cache: str | None = None
_lang_cache_ts: float = 0.0
_LANG_CACHE_TTL = 10.0  # seconds


# ── Public API ───────────────────────────────────────────────────────────────


def t(key: str, lang: str | None = None, **kwargs: Any) -> str:
    """Translate *key* into the given language with interpolation.

    Args:
        key:    Dot-notation key, e.g. ``"media.playing_radio"``.
        lang:   Language code (``"en"``, ``"uk"``).
                If *None*, reads from ``core.yaml system.language``.
        **kwargs: Interpolation variables,
                  e.g. ``station="Jazz FM"`` → ``{station}`` in template.

    Returns:
        Translated string with variables substituted.
        Falls back to ``"en"`` if the key is missing in *lang*.
        Returns the raw *key* if missing from all locales.
    """
    if lang is None:
        lang = get_voice_lang()

    translations = _get_locale(lang)
    value = translations.get(key)

    # Fallback to English
    if value is None and lang != "en":
        en = _get_locale("en")
        value = en.get(key)

    # Fallback to raw key
    if value is None:
        return key

    if kwargs or "{" in value:
        try:
            return value.format_map(defaultdict(str, kwargs))
        except (KeyError, ValueError, IndexError):
            return value

    return value


def get_system_lang() -> str:
    """Read ``system.language`` from core.yaml (cached for 10 s).

    Used for UI translations (frontend widgets, settings pages).
    For voice/TTS responses use :func:`get_voice_lang` instead.
    """
    global _lang_cache, _lang_cache_ts

    now = time.monotonic()
    if _lang_cache is not None and (now - _lang_cache_ts) < _LANG_CACHE_TTL:
        return _lang_cache

    try:
        from core.config_writer import get_value
        lang = get_value("system", "language", "en") or "en"
    except Exception:
        lang = "en"

    _lang_cache = lang
    _lang_cache_ts = now
    return lang


_voice_lang_cache: str | None = None
_voice_lang_cache_ts: float = 0.0


def get_voice_lang() -> str:
    """Read ``voice.tts.primary.lang`` — the TTS output language.

    Used by voice handlers for ``t()`` translations that will be spoken aloud.
    Falls back to :func:`get_system_lang` if TTS lang is not configured.
    """
    global _voice_lang_cache, _voice_lang_cache_ts

    now = time.monotonic()
    if _voice_lang_cache is not None and (now - _voice_lang_cache_ts) < _LANG_CACHE_TTL:
        return _voice_lang_cache

    try:
        from core.config_writer import read_config
        lang = read_config().get("voice", {}).get("tts", {}).get("primary", {}).get("lang", "")
        if not lang:
            lang = get_system_lang()
    except Exception:
        lang = get_system_lang()

    _voice_lang_cache = lang
    _voice_lang_cache_ts = now
    return lang


def register_module_locales(module_name: str, locales_dir: Path) -> None:
    """Register locale files from a user module directory.

    Files must be ``{lang}.json`` inside *locales_dir*.
    Keys are prefixed with *module_name* to avoid collisions:
    ``"forecast_error"`` → ``"weather-module.forecast_error"``.
    """
    if not locales_dir.is_dir():
        return

    with _lock:
        for path in locales_dir.glob("*.json"):
            lang = path.stem
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("i18n: failed to load %s: %s", path, exc)
                continue

            flat = _flatten(raw)
            prefixed = {f"{module_name}.{k}": v for k, v in flat.items()}

            if lang not in _cache:
                _cache[lang] = {}
            _cache[lang].update(prefixed)

        logger.info(
            "i18n: registered locales for '%s' from %s", module_name, locales_dir,
        )


def locale_exists(lang: str) -> bool:
    """Check if a locale file exists for the given language code."""
    return (_LOCALES_DIR / f"{lang}.json").is_file()


def reload_locales() -> None:
    """Clear all cached translations. Next ``t()`` call reloads from disk."""
    global _lang_cache, _lang_cache_ts
    with _lock:
        _cache.clear()
    _lang_cache = None
    _lang_cache_ts = 0.0
    logger.info("i18n: locale cache cleared")


# ── Internal helpers ─────────────────────────────────────────────────────────


def _get_locale(lang: str) -> dict[str, str]:
    """Return the flat translation dict for *lang*, loading on first access."""
    if lang in _cache:
        return _cache[lang]

    with _lock:
        # Double-check after acquiring lock
        if lang in _cache:
            return _cache[lang]

        path = _LOCALES_DIR / f"{lang}.json"
        if not path.is_file():
            _cache[lang] = {}
            return _cache[lang]

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            _cache[lang] = _flatten(raw)
            logger.info("i18n: loaded %s (%d keys)", path.name, len(_cache[lang]))
        except Exception as exc:
            logger.error("i18n: failed to load %s: %s", path, exc)
            _cache[lang] = {}

    return _cache[lang]


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten a nested dict into dot-notation keys.

    ``{"media": {"pause": "Paused"}}`` → ``{"media.pause": "Paused"}``
    Flat dicts pass through unchanged.
    """
    result: dict[str, str] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten(value, full_key))
        else:
            result[full_key] = str(value)
    return result
