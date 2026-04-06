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


async def translate_to_en(text: str) -> str:
    """Translate a single phrase to English via LLM.  Returns original on failure."""
    if not text:
        return ""
    if all(ord(c) < 128 for c in text):
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
    """Translate a list of smart-home keywords to English via LLM."""
    if not keywords:
        return []

    all_ascii = all(all(ord(c) < 128 for c in kw) for kw in keywords)
    if all_ascii:
        return [kw.lower().strip() for kw in keywords]

    try:
        from core.llm import llm_call

        text = ", ".join(keywords)
        result = await llm_call(
            f"Translate these smart home keywords to English. Return ONLY a comma-separated list of English words, nothing else: {text}",
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


async def get_entity_patterns(factory: Any, entity_ref: str) -> list[str]:
    """Fetch generated English patterns for an entity from DB."""
    try:
        from core.registry.models import IntentPattern

        async with factory() as session:
            result = await session.execute(
                select(IntentPattern.pattern).where(
                    IntentPattern.entity_ref == entity_ref,
                    IntentPattern.lang == "en",
                )
            )
            return [r[0] for r in result.all()]
    except Exception:
        return []


# ── Entity-change invalidation ──────────────────────────────────────────────


async def on_entity_changed(entity_type: str, entity_id: int | str, action: str) -> None:
    """Generate/delete patterns + invalidate caches after entity data change."""
    try:
        from system_modules.llm_engine.pattern_generator import get_pattern_generator

        gen = get_pattern_generator()
        if action == "deleted":
            await gen.delete_for_entity(entity_type, entity_id)
        else:
            await gen.generate_for_entity(entity_type, entity_id)
    except Exception as exc:
        logger.debug("Pattern generation failed: %s", exc)

    try:
        from system_modules.llm_engine.intent_compiler import get_intent_compiler

        await get_intent_compiler().full_reload()
    except Exception:
        pass

    try:
        from system_modules.llm_engine.intent_router import get_intent_router

        get_intent_router().refresh_system_prompt()
    except Exception:
        pass

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
