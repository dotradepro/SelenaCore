#!/usr/bin/env python3
"""
scripts/migrate_english_only_patterns.py — Migrate to English-only patterns.

Removes all non-English intent patterns and Ukrainian vocab entries.
Regenerates entity patterns (radio, device, scene) in English only.
Seeds default weather module patterns.

Usage:
    python scripts/migrate_english_only_patterns.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    from sqlalchemy import delete, select, func
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from core.registry.models import (
        Base, IntentDefinition, IntentPattern, IntentVocab,
    )
    from core.config import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.db_url)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # 1. Delete all non-English patterns
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                delete(IntentPattern).where(IntentPattern.lang != "en")
            )
            deleted = result.rowcount
            logger.info("Deleted %d non-English patterns", deleted)

    # 2. Delete Ukrainian vocab entries
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                delete(IntentVocab).where(IntentVocab.lang != "en")
            )
            deleted = result.rowcount
            logger.info("Deleted %d non-English vocab entries", deleted)

    # 3. Regenerate entity patterns (English only)
    try:
        from system_modules.llm_engine.pattern_generator import PatternGenerator
        gen = PatternGenerator(session_factory)
        total = await gen.regenerate_all()
        logger.info("Regenerated %d English-only entity patterns", total)
    except Exception as exc:
        logger.warning("Entity pattern regeneration failed: %s", exc)

    # 4. Seed default weather patterns
    await _seed_weather_patterns(session_factory)

    # 5. Summary
    async with session_factory() as session:
        count_result = await session.execute(
            select(func.count()).select_from(IntentPattern)
        )
        total_patterns = count_result.scalar() or 0

        en_count_result = await session.execute(
            select(func.count()).where(IntentPattern.lang == "en")
        )
        en_count = en_count_result.scalar() or 0

        non_en_result = await session.execute(
            select(func.count()).where(IntentPattern.lang != "en")
        )
        non_en = non_en_result.scalar() or 0

    logger.info("Final state: %d total patterns (%d en, %d non-en)", total_patterns, en_count, non_en)

    await engine.dispose()
    logger.info("Migration complete!")


async def _seed_weather_patterns(session_factory) -> None:
    """Seed default English patterns for weather module."""
    from sqlalchemy import select
    from core.registry.models import IntentDefinition, IntentPattern

    weather_intents = {
        "weather.current": {
            "description": "Get current weather",
            "patterns": [
                "(?:current|right now)\\s+weather",
                "(?:what(?:'s| is))\\s+(?:the\\s+)?weather",
                "weather\\s+(?:right )?now",
                "how(?:'s| is)\\s+(?:the\\s+)?weather",
            ],
        },
        "weather.today": {
            "description": "Get weather forecast for today",
            "patterns": [
                "weather\\s+today",
                "today(?:'s| is)?\\s+(?:weather|forecast)",
                "weather\\s+forecast\\s+(?:for\\s+)?today",
            ],
        },
        "weather.forecast": {
            "description": "Get weather forecast for multiple days",
            "patterns": [
                "weather\\s+(?:for\\s+)?(?:3|three)\\s+days",
                "weather\\s+forecast",
                "weekly\\s+(?:weather|forecast)",
                "(?:3|three)\\s+day\\s+(?:weather|forecast)",
            ],
        },
    }

    async with session_factory() as session:
        async with session.begin():
            for intent_name, data in weather_intents.items():
                # Ensure definition exists
                result = await session.execute(
                    select(IntentDefinition).where(IntentDefinition.intent == intent_name)
                )
                idef = result.scalar_one_or_none()
                if idef is None:
                    idef = IntentDefinition(
                        intent=intent_name,
                        module="weather-service",
                        noun_class="WEATHER",
                        verb="query",
                        priority=5,
                        description=data["description"],
                        source="system",
                    )
                    session.add(idef)
                    await session.flush()

                # Add patterns (skip if already exist)
                for pattern_str in data["patterns"]:
                    existing = await session.execute(
                        select(IntentPattern).where(
                            IntentPattern.intent_id == idef.id,
                            IntentPattern.pattern == pattern_str,
                            IntentPattern.lang == "en",
                        )
                    )
                    if existing.scalar_one_or_none() is None:
                        session.add(IntentPattern(
                            intent_id=idef.id,
                            lang="en",
                            pattern=pattern_str,
                            source="system",
                        ))

    logger.info("Seeded weather patterns")


if __name__ == "__main__":
    asyncio.run(main())
