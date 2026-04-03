"""
system_modules/llm_engine/pattern_generator.py — Auto-generate intent patterns from entity data.

When a user adds a radio station, device, or scene — this generator creates
regex patterns in the intent_patterns DB table so they match at 0ms (no LLM).

Entity types supported:
  radio_station → media.play_radio_name patterns
  device        → device.on / device.off patterns
  scene         → automation.run_scene patterns
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class PatternGenerator:
    """Generates intent_patterns rows from entity data in the DB."""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    async def generate_for_entity(
        self, entity_type: str, entity_id: int | str,
    ) -> int:
        """Generate patterns for a specific entity. Returns count of patterns created."""
        async with self._sf() as session:
            async with session.begin():
                if entity_type == "radio_station":
                    return await self._gen_radio_station(session, int(entity_id))
                elif entity_type == "device":
                    return await self._gen_device(session, str(entity_id))
                elif entity_type == "scene":
                    return await self._gen_scene(session, int(entity_id))
        return 0

    async def delete_for_entity(
        self, entity_type: str, entity_id: int | str,
    ) -> int:
        """Delete auto-generated patterns for an entity. Returns count deleted."""
        from core.registry.models import IntentPattern

        ref = f"{entity_type}:{entity_id}"
        async with self._sf() as session:
            async with session.begin():
                result = await session.execute(
                    delete(IntentPattern).where(IntentPattern.entity_ref == ref)
                )
                count = result.rowcount
        if count:
            logger.info("Deleted %d auto-patterns for %s", count, ref)
        return count

    async def regenerate_all(self, entity_type: str | None = None) -> int:
        """Regenerate all auto-entity patterns. Returns total count."""
        from core.registry.models import IntentPattern

        total = 0

        # Delete existing auto-entity patterns
        async with self._sf() as session:
            async with session.begin():
                stmt = delete(IntentPattern).where(IntentPattern.source == "auto_entity")
                if entity_type:
                    stmt = stmt.where(IntentPattern.entity_ref.like(f"{entity_type}:%"))
                await session.execute(stmt)

        # Regenerate for each entity type
        types = [entity_type] if entity_type else ["radio_station", "device", "scene"]

        for etype in types:
            if etype == "radio_station":
                total += await self._regenerate_radio_stations()
            elif etype == "device":
                total += await self._regenerate_devices()
            elif etype == "scene":
                total += await self._regenerate_scenes()

        logger.info("Regenerated %d auto-entity patterns (type=%s)", total, entity_type or "all")
        return total

    # ── Radio stations ──────────────────────────────────────────────────

    async def _gen_radio_station(self, session: AsyncSession, station_id: int) -> int:
        """Generate patterns for one radio station."""
        from core.registry.models import RadioStation, IntentPattern, IntentDefinition

        station = await session.get(RadioStation, station_id)
        if not station or not station.enabled:
            return 0

        # Get or create intent definition
        idef = await self._ensure_definition(
            session, "media.play_radio_name", "media-player", "MEDIA", "play", 10,
            "Play specific radio station by name",
        )

        # Delete old patterns for this station
        ref = f"radio_station:{station_id}"
        await session.execute(
            delete(IntentPattern).where(IntentPattern.entity_ref == ref)
        )

        # Load verbs from vocab
        verbs_en = await self._get_vocab_words(session, "en", "verb", "play")
        verbs_uk = await self._get_vocab_words(session, "uk", "verb", "play")

        count = 0

        # EN patterns
        if station.name_en:
            name_esc = re.escape(station.name_en.lower())
            verb_alt = "|".join(re.escape(v) for v in verbs_en) if verbs_en else "play|put on|turn on"
            pattern = f"(?:{verb_alt})\\s+(?:radio\\s+)?(?:station\\s+)?{name_esc}"
            session.add(IntentPattern(
                intent_id=idef.id, lang="en", pattern=pattern,
                source="auto_entity", entity_ref=ref,
            ))
            count += 1

        # UK patterns
        if station.name_user:
            name_esc = re.escape(station.name_user.lower())
            verb_alt = "|".join(re.escape(v) for v in verbs_uk) if verbs_uk else "увімкни|включи|постав"
            pattern = f"(?:{verb_alt})\\s+(?:радіо\\s+)?(?:станцію\\s+)?{name_esc}"
            session.add(IntentPattern(
                intent_id=idef.id, lang="uk", pattern=pattern,
                source="auto_entity", entity_ref=ref,
            ))
            count += 1

        return count

    async def _regenerate_radio_stations(self) -> int:
        """Regenerate patterns for all enabled radio stations."""
        from core.registry.models import RadioStation

        total = 0
        async with self._sf() as session:
            result = await session.execute(
                select(RadioStation.id).where(RadioStation.enabled == True)
            )
            ids = [r[0] for r in result.all()]

        for sid in ids:
            async with self._sf() as session:
                async with session.begin():
                    total += await self._gen_radio_station(session, sid)
        return total

    # ── Devices ─────────────────────────────────────────────────────────

    async def _gen_device(self, session: AsyncSession, device_id: str) -> int:
        """Generate patterns for one device."""
        from core.registry.models import Device, IntentPattern, IntentDefinition

        device = await session.get(Device, device_id)
        if not device:
            return 0

        ref = f"device:{device_id}"
        await session.execute(
            delete(IntentPattern).where(IntentPattern.entity_ref == ref)
        )

        # Get device name(s)
        name = device.name.lower()
        name_esc = re.escape(name)
        keywords_en = device.get_keywords_en()

        # Location suffix
        loc_part = ""
        if device.location:
            loc_esc = re.escape(device.location.lower())
            loc_part = f"(?:\\s+(?:in|on)\\s+(?:the\\s+)?{loc_esc})?"

        verbs_on_en = await self._get_vocab_words(session, "en", "verb", "on")
        verbs_off_en = await self._get_vocab_words(session, "en", "verb", "off")
        verbs_on_uk = await self._get_vocab_words(session, "uk", "verb", "on")
        verbs_off_uk = await self._get_vocab_words(session, "uk", "verb", "off")

        count = 0

        # device.on
        idef_on = await self._ensure_definition(
            session, "device.on", "", "DEVICE", "on", 100,
            "Turn on a device",
        )

        # EN
        verb_alt = "|".join(re.escape(v) for v in verbs_on_en) if verbs_on_en else "turn on|switch on"
        pattern = f"(?:{verb_alt})\\s+(?:the\\s+)?{name_esc}{loc_part}"
        session.add(IntentPattern(
            intent_id=idef_on.id, lang="en", pattern=pattern,
            source="auto_entity", entity_ref=ref,
        ))
        count += 1

        # UK — use keywords_user if available
        keywords_user = device.get_keywords_user()
        uk_name = re.escape(keywords_user[0].lower()) if keywords_user else name_esc
        verb_alt_uk = "|".join(re.escape(v) for v in verbs_on_uk) if verbs_on_uk else "увімкни|включи"
        pattern_uk = f"(?:{verb_alt_uk})\\s+{uk_name}"
        session.add(IntentPattern(
            intent_id=idef_on.id, lang="uk", pattern=pattern_uk,
            source="auto_entity", entity_ref=ref,
        ))
        count += 1

        # device.off
        idef_off = await self._ensure_definition(
            session, "device.off", "", "DEVICE", "off", 100,
            "Turn off a device",
        )

        verb_alt = "|".join(re.escape(v) for v in verbs_off_en) if verbs_off_en else "turn off|switch off"
        pattern = f"(?:{verb_alt})\\s+(?:the\\s+)?{name_esc}{loc_part}"
        session.add(IntentPattern(
            intent_id=idef_off.id, lang="en", pattern=pattern,
            source="auto_entity", entity_ref=ref,
        ))
        count += 1

        verb_alt_uk = "|".join(re.escape(v) for v in verbs_off_uk) if verbs_off_uk else "вимкни"
        pattern_uk = f"(?:{verb_alt_uk})\\s+{uk_name}"
        session.add(IntentPattern(
            intent_id=idef_off.id, lang="uk", pattern=pattern_uk,
            source="auto_entity", entity_ref=ref,
        ))
        count += 1

        return count

    async def _regenerate_devices(self) -> int:
        """Regenerate patterns for all devices with entity_type."""
        from core.registry.models import Device

        total = 0
        async with self._sf() as session:
            result = await session.execute(
                select(Device.device_id).where(Device.entity_type.isnot(None))
            )
            ids = [r[0] for r in result.all()]

        for did in ids:
            async with self._sf() as session:
                async with session.begin():
                    total += await self._gen_device(session, did)
        return total

    # ── Scenes ──────────────────────────────────────────────────────────

    async def _gen_scene(self, session: AsyncSession, scene_id: int) -> int:
        """Generate patterns for one scene."""
        from core.registry.models import Scene, IntentPattern

        scene = await session.get(Scene, scene_id)
        if not scene or not scene.enabled:
            return 0

        idef = await self._ensure_definition(
            session, "automation.run_scene", "automation-engine", "AUTOMATION", "play", 10,
            "Activate a scene by name",
        )

        ref = f"scene:{scene_id}"
        await session.execute(
            delete(IntentPattern).where(IntentPattern.entity_ref == ref)
        )

        count = 0

        if scene.name_en:
            name_esc = re.escape(scene.name_en.lower())
            pattern = f"(?:activate|run|start|launch)\\s+(?:scene\\s+)?{name_esc}"
            session.add(IntentPattern(
                intent_id=idef.id, lang="en", pattern=pattern,
                source="auto_entity", entity_ref=ref,
            ))
            count += 1

        if scene.name_user:
            name_esc = re.escape(scene.name_user.lower())
            pattern = f"(?:увімкни|запусти|активуй)\\s+(?:сцену\\s+)?{name_esc}"
            session.add(IntentPattern(
                intent_id=idef.id, lang="uk", pattern=pattern,
                source="auto_entity", entity_ref=ref,
            ))
            count += 1

        return count

    async def _regenerate_scenes(self) -> int:
        """Regenerate patterns for all enabled scenes."""
        from core.registry.models import Scene

        total = 0
        async with self._sf() as session:
            result = await session.execute(
                select(Scene.id).where(Scene.enabled == True)
            )
            ids = [r[0] for r in result.all()]

        for sid in ids:
            async with self._sf() as session:
                async with session.begin():
                    total += await self._gen_scene(session, sid)
        return total

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _ensure_definition(
        self, session: AsyncSession,
        intent: str, module: str, noun_class: str, verb: str,
        priority: int, description: str,
    ):
        """Get existing or create an intent definition."""
        from core.registry.models import IntentDefinition

        result = await session.execute(
            select(IntentDefinition).where(IntentDefinition.intent == intent)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        idef = IntentDefinition(
            intent=intent, module=module, noun_class=noun_class,
            verb=verb, priority=priority, description=description,
            source="auto",
        )
        session.add(idef)
        await session.flush()
        return idef

    async def _get_vocab_words(
        self, session: AsyncSession, lang: str, category: str, key: str,
    ) -> list[str]:
        """Load vocabulary words from intent_vocab table."""
        from core.registry.models import IntentVocab

        result = await session.execute(
            select(IntentVocab).where(
                IntentVocab.lang == lang,
                IntentVocab.category == category,
                IntentVocab.key == key,
            )
        )
        entry = result.scalar_one_or_none()
        if entry:
            return entry.get_words()
        return []


# ── Singleton ────────────────────────────────────────────────────────────

_generator: PatternGenerator | None = None


def get_pattern_generator() -> PatternGenerator:
    """Get or create PatternGenerator singleton."""
    global _generator
    if _generator is None:
        from core.module_loader.sandbox import get_sandbox
        sf = get_sandbox()._session_factory
        if sf is None:
            raise RuntimeError("session_factory not available yet")
        _generator = PatternGenerator(sf)
    return _generator
