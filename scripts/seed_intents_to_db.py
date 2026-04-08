#!/usr/bin/env python3
"""
scripts/seed_intents_to_db.py — Migrate YAML intents + vocab to DB

Reads config/intents/definitions.yaml + vocab/*.yaml and populates:
  - intent_definitions (28 intents)
  - intent_patterns (regex patterns per lang)
  - intent_vocab (verbs, nouns, params, locations)

Also seeds FastMatcher default rules as high-priority intents.

Idempotent: uses INSERT ... ON CONFLICT for system-sourced data.
Run once, then config/intents/ can be deleted.

Usage:
    python scripts/seed_intents_to_db.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _load_yaml(path: Path) -> dict:
    """Load YAML file."""
    import yaml
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def main() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from core.registry.models import (
        Base, IntentDefinition, IntentPattern, IntentVocab,
    )
    from core.config import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.db_url)

    # Create tables if needed
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    intents_dir = PROJECT_ROOT / "config" / "intents"
    defs_path = intents_dir / "definitions.yaml"
    vocab_en_path = intents_dir / "vocab" / "en.yaml"
    vocab_uk_path = intents_dir / "vocab" / "uk.yaml"

    if not defs_path.exists():
        logger.error("definitions.yaml not found at %s", defs_path)
        return

    defs = _load_yaml(defs_path)
    vocab_en = _load_yaml(vocab_en_path) if vocab_en_path.exists() else {}

    async with session_factory() as session:
        async with session.begin():
            # 1. Seed intent_vocab (English only — all patterns are English)
            vocab_count = await _seed_vocab(session, "en", vocab_en)
            logger.info("Seeded %d vocab entries (English only)", vocab_count)

            # 2. Seed intent_definitions + intent_patterns
            intents = defs.get("intents", {})
            intent_count = 0
            pattern_count = 0
            for intent_name, intent_data in intents.items():
                idef = await _upsert_definition(session, intent_name, intent_data)
                patterns = await _seed_patterns_for_intent(session, idef, intent_data)
                intent_count += 1
                pattern_count += patterns

            logger.info("Seeded %d intents with %d patterns", intent_count, pattern_count)

            # 3. Seed FastMatcher default rules
            fm_count = await _seed_fast_matcher_rules(session)
            logger.info("Seeded %d FastMatcher rules as high-priority intents", fm_count)

    await engine.dispose()
    logger.info("Seed complete!")


async def _seed_vocab(session, lang: str, vocab_data: dict) -> int:
    """Insert vocab entries for one language."""
    from sqlalchemy import select
    from core.registry.models import IntentVocab

    count = 0
    for category in ("verbs", "nouns", "params", "locations"):
        items = vocab_data.get(category, {})
        # Normalize category name
        cat = category.rstrip("s") if category != "params" else "param"
        if category == "locations":
            cat = "location"

        for key, value in items.items():
            if isinstance(value, dict):
                words = value.get("exact", [])
                stems = value.get("stem", [])
            elif isinstance(value, list):
                words = value
                stems = []
            elif isinstance(value, str):
                # Special types like "__NUMBER__", "__FREETEXT__"
                words = [value]
                stems = []
            else:
                continue

            # Check if exists
            result = await session.execute(
                select(IntentVocab).where(
                    IntentVocab.lang == lang,
                    IntentVocab.category == cat,
                    IntentVocab.key == key,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.set_words(words)
                existing.set_stems(stems)
            else:
                entry = IntentVocab(
                    lang=lang, category=cat, key=key,
                )
                entry.set_words(words)
                entry.set_stems(stems)
                session.add(entry)

            count += 1

    return count


async def _upsert_definition(session, intent_name: str, data: dict):
    """Insert or update an intent definition."""
    from sqlalchemy import select
    from core.registry.models import IntentDefinition

    result = await session.execute(
        select(IntentDefinition).where(IntentDefinition.intent == intent_name)
    )
    existing = result.scalar_one_or_none()

    params = data.get("params", {})
    params_json = json.dumps(params)

    if existing:
        existing.module = data.get("module", "")
        existing.noun_class = data.get("noun_class", "")
        existing.verb = data.get("verb", "")
        existing.priority = data.get("priority", 5)
        existing.description = data.get("description", "")
        existing.params_schema = params_json
        await session.flush()
        return existing
    else:
        idef = IntentDefinition(
            intent=intent_name,
            module=data.get("module", ""),
            noun_class=data.get("noun_class", ""),
            verb=data.get("verb", ""),
            priority=data.get("priority", 5),
            description=data.get("description", ""),
            source="system",
            params_schema=params_json,
        )
        session.add(idef)
        await session.flush()
        return idef


async def _seed_patterns_for_intent(session, idef, data: dict) -> int:
    """Seed patterns for an intent from overrides."""
    from sqlalchemy import select, delete
    from core.registry.models import IntentPattern

    # Delete existing system patterns for this intent
    await session.execute(
        delete(IntentPattern).where(
            IntentPattern.intent_id == idef.id,
            IntentPattern.source.in_(["manual", "template"]),
        )
    )

    count = 0
    overrides = data.get("overrides", {})
    # Only seed English patterns — all patterns are English-only
    en_patterns = overrides.get("en", [])
    for pattern in en_patterns:
        p = IntentPattern(
            intent_id=idef.id,
            lang="en",
            pattern=pattern,
            source="manual",
        )
        session.add(p)
        count += 1

    return count


async def _seed_fast_matcher_rules(session) -> int:
    """Seed FastMatcher default rules as high-priority intent definitions."""
    from sqlalchemy import select, delete
    from core.registry.models import IntentDefinition, IntentPattern

    # FastMatcher rules — English-only patterns
    # All patterns are in English. Non-English input falls through to LLM.
    rules = [
        {
            "intent": "device.on",
            "module": "device-control",
            "noun_class": "DEVICE",
            "verb": "on",
            "priority": 100,
            "description": "Turn on a device (light, etc.)",
            "patterns": {
                "en": [
                    r"(?:turn on|switch on)\s+(?:the\s+)?(?:light|lamp|lights)",
                ],
            },
        },
        {
            "intent": "device.off",
            "module": "device-control",
            "noun_class": "DEVICE",
            "verb": "off",
            "priority": 100,
            "description": "Turn off a device (light, etc.)",
            "patterns": {
                "en": [
                    r"(?:turn off|switch off)\s+(?:the\s+)?(?:light|lamp|lights)",
                ],
            },
        },
        {
            "intent": "privacy_on",
            "module": "voice-core",
            "noun_class": "DEVICE",
            "verb": "on",
            "priority": 100,
            "description": "Enable privacy mode (stop listening)",
            "patterns": {
                "en": [r"(?:privacy on|stop listening|enable.*privac)"],
            },
        },
        {
            "intent": "privacy_off",
            "module": "voice-core",
            "noun_class": "DEVICE",
            "verb": "off",
            "priority": 100,
            "description": "Disable privacy mode (start listening)",
            "patterns": {
                "en": [r"(?:privacy off|start listening|disable.*privac)"],
            },
        },
        {
            "intent": "weather.current",
            "module": "weather-service",
            "noun_class": "WEATHER",
            "verb": "query",
            "priority": 5,
            "description": "Get current weather",
            "patterns": {
                "en": [
                    r"(?:current|right now)\s+weather",
                    r"(?:what(?:'s| is))\s+(?:the\s+)?weather",
                    r"weather\s+(?:right )?now",
                    r"how(?:'s| is)\s+(?:the\s+)?weather",
                ],
            },
        },
        {
            "intent": "weather.today",
            "module": "weather-service",
            "noun_class": "WEATHER",
            "verb": "query",
            "priority": 5,
            "description": "Get weather forecast for today",
            "patterns": {
                "en": [
                    r"weather\s+today",
                    r"today(?:'s| is)?\s+(?:weather|forecast)",
                    r"weather\s+forecast\s+(?:for\s+)?today",
                ],
            },
        },
        {
            "intent": "weather.forecast",
            "module": "weather-service",
            "noun_class": "WEATHER",
            "verb": "query",
            "priority": 5,
            "description": "Get weather forecast for multiple days",
            "patterns": {
                "en": [
                    r"weather\s+(?:for\s+)?(?:3|three)\s+days",
                    r"weather\s+forecast",
                    r"weekly\s+(?:weather|forecast)",
                    r"(?:3|three)\s+day\s+(?:weather|forecast)",
                ],
            },
        },
        # ── Climate (air-conditioner) intents — owned by device-control ──
        {
            "intent": "device.set_temperature",
            "module": "device-control",
            "noun_class": "CLIMATE",
            "verb": "set",
            "priority": 100,
            "description": "Set the target temperature on a climate device",
            "patterns": {
                "en": [
                    r"set\s+(?:the\s+)?temperature\s+(?:to|at)\s+(?P<level>\d{1,2})(?:\s+in\s+(?P<location>[\w\s]+?))?$",
                    r"(?:make it|set it to)\s+(?P<level>\d{1,2})(?:\s+degrees?)?(?:\s+in\s+(?P<location>[\w\s]+?))?$",
                ],
            },
        },
        {
            "intent": "device.set_mode",
            "module": "device-control",
            "noun_class": "CLIMATE",
            "verb": "set",
            "priority": 100,
            "description": "Switch climate device mode (cool/heat/dry/fan/auto)",
            "patterns": {
                "en": [
                    r"(?:switch|set|turn)\s+(?:the\s+)?(?:ac|air\s*conditioner|climate)\s+(?:to\s+)?(?P<mode>auto|cool|cooling|dry|fan|heat|heating)(?:\s+mode)?(?:\s+in\s+(?P<location>[\w\s]+?))?$",
                    r"(?:switch|set)\s+(?P<location>[\w\s]+?)\s+to\s+(?P<mode>auto|cool|cooling|dry|fan|heat|heating)(?:\s+mode)?$",
                ],
            },
        },
        {
            "intent": "device.set_fan_speed",
            "module": "device-control",
            "noun_class": "CLIMATE",
            "verb": "set",
            "priority": 100,
            "description": "Set climate device fan speed",
            "patterns": {
                "en": [
                    r"(?:set|change)\s+(?:the\s+)?fan\s+(?:speed\s+)?(?:to\s+)?(?P<level>auto|low|medium|high|min|minimum|max|maximum)(?:\s+in\s+(?P<location>[\w\s]+?))?$",
                ],
            },
        },
    ]

    count = 0
    for rule in rules:
        # Check if already in definitions.yaml intents (avoid duplicates)
        result = await session.execute(
            select(IntentDefinition).where(IntentDefinition.intent == rule["intent"])
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update priority to FastMatcher level if lower
            if existing.priority < rule["priority"]:
                existing.priority = rule["priority"]
            # Add patterns that don't exist
            for lang, patterns in rule["patterns"].items():
                for pattern in patterns:
                    # Check if pattern already exists
                    check = await session.execute(
                        select(IntentPattern).where(
                            IntentPattern.intent_id == existing.id,
                            IntentPattern.lang == lang,
                            IntentPattern.pattern == pattern,
                        )
                    )
                    if check.scalar_one_or_none() is None:
                        session.add(IntentPattern(
                            intent_id=existing.id,
                            lang=lang,
                            pattern=pattern,
                            source="manual",
                        ))
                        count += 1
        else:
            idef = IntentDefinition(
                intent=rule["intent"],
                module=rule["module"],
                noun_class=rule["noun_class"],
                verb=rule["verb"],
                priority=rule["priority"],
                description=rule["description"],
                source="system",
            )
            session.add(idef)
            await session.flush()

            for lang, patterns in rule["patterns"].items():
                for pattern in patterns:
                    session.add(IntentPattern(
                        intent_id=idef.id,
                        lang=lang,
                        pattern=pattern,
                        source="manual",
                    ))
                    count += 1

    return count


if __name__ == "__main__":
    asyncio.run(main())
