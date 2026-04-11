"""
core/api/helpers.py — Shared helpers for API route handlers.

Centralises translation, pattern fetching, and entity-change invalidation
that were previously duplicated across devices.py, scenes.py, and radio.py.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)


# ── Translation helpers ─────────────────────────────────────────────────────


def _detect_text_lang(text: str) -> str:
    """Rough language guess from character ranges."""
    for ch in text:
        cp = ord(ch)
        if 0x0400 <= cp <= 0x04FF:
            return "uk"  # Cyrillic → assume Ukrainian (configurable)
    return "en"


async def translate_to_en(text: str) -> str:
    """Translate a single phrase to English.

    Priority: local CTranslate2 model → LLM fallback → original text.
    """
    if not text:
        return ""
    if all(ord(c) < 128 for c in text):
        return text

    # Try local translator first (~50ms, offline)
    from core.config_writer import get_value
    if get_value("translation", "enabled", False):
        from core.translation.local_translator import get_input_translator
        t = get_input_translator()
        if t.is_available():
            lang = _detect_text_lang(text)
            return t.to_english(text, lang)

    # Fallback to LLM if configured
    if not get_value("translation", "fallback_to_llm", True):
        return text
    try:
        from core.llm import llm_call
        result = await llm_call(
            f"Translate to English (single phrase, no quotes): {text}",
            prompt_key="translate",
            temperature=0.0,
            timeout=10.0,
        )
        return result.strip().strip('"').strip("'") if result else text
    except Exception:
        return text


async def translate_keywords_to_en(keywords: list[str]) -> list[str]:
    """Translate a list of smart-home keywords to English.

    Priority: local CTranslate2 batch → LLM fallback → original keywords.
    """
    if not keywords:
        return []

    all_ascii = all(all(ord(c) < 128 for c in kw) for kw in keywords)
    if all_ascii:
        return [kw.lower().strip() for kw in keywords]

    # Try local translator first (batch mode, ~50ms)
    from core.config_writer import get_value
    if get_value("translation", "enabled", False):
        from core.translation.local_translator import get_input_translator
        t = get_input_translator()
        if t.is_available():
            sample = next((k for k in keywords if k.strip()), "")
            lang = _detect_text_lang(sample) if sample else "uk"
            return [r.lower().strip()
                    for r in t.keywords_to_english(
                        [kw.strip() for kw in keywords], lang,
                    )]

    # Fallback to LLM
    if not get_value("translation", "fallback_to_llm", True):
        return [kw.lower().strip() for kw in keywords]
    try:
        from core.llm import llm_call
        text = ", ".join(keywords)
        result = await llm_call(
            f"Translate these smart home keywords to English. "
            f"Return ONLY a comma-separated list: {text}",
            prompt_key="translate",
            temperature=0.0,
            timeout=10.0,
        )
        if result:
            translated = [w.strip().lower() for w in result.split(",") if w.strip()]
            if translated:
                return translated
    except Exception as exc:
        logger.warning("Keywords translation failed: %s", exc)

    return [kw.lower().strip() for kw in keywords]


# ── Pattern helpers ─────────────────────────────────────────────────────────


async def get_entity_patterns(factory: Any, entity_ref: str) -> list[str]:  # noqa: ARG001
    """Legacy shim — always returns an empty list.

    Patterns were killed with the FastMatcher. The UI still calls this
    to render a per-entity pattern list; we keep the function so no
    route needs migration, but there is no table to query anymore.
    """
    return []


# ── Entity-change invalidation ──────────────────────────────────────────────


async def on_entity_changed(entity_type: str, entity_id: int | str, action: str) -> None:
    """Refresh the in-memory indexes after a registry CRUD.

    With FastMatcher / PatternGenerator gone, the only caches to refresh
    are:
      1. ``IntentCompiler`` — lists intent definitions for the LLM catalog.
      2. ``PatternGenerator.rebuild()`` — unique-name index used by
         device-control to map LLM-returned ``name_en`` back to a device.
      3. ``REGISTRY_ENTITY_CHANGED`` event — broadcast for other modules.
    """
    try:
        from system_modules.llm_engine.intent_compiler import get_intent_compiler

        await get_intent_compiler().full_reload()
    except Exception as exc:
        logger.debug("IntentCompiler reload failed: %s", exc)

    if entity_type == "device":
        try:
            from system_modules.llm_engine.pattern_generator import get_pattern_generator

            await get_pattern_generator().rebuild()
        except Exception as exc:
            logger.debug("PatternGenerator rebuild failed: %s", exc)

    try:
        from core.eventbus.bus import get_event_bus
        from core.eventbus.types import REGISTRY_ENTITY_CHANGED

        await get_event_bus().publish(
            type=REGISTRY_ENTITY_CHANGED,
            source="core.api",
            payload={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "action": action,
            },
        )
    except Exception:
        pass
