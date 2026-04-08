"""
system_modules/llm_engine/pattern_generator.py — Auto-generate intent patterns from entity data.

When a user adds a radio station, device, or scene — this generator creates
English-only regex patterns in the intent_patterns DB table so they match at 0ms (no LLM).

All patterns are in English regardless of system language. LLM is used to generate
natural English command phrases, with hardcoded templates as fallback.

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

from system_modules.llm_engine.pattern_utils import (
    deduplicate_pattern,
    phrase_to_regex,
    validate_pattern,
)

logger = logging.getLogger(__name__)

# Max patterns per entity (prevent unbounded growth)
MAX_PATTERNS_PER_ENTITY = 5

# Hardcoded English-only system prompt for entity pattern generation.
# NOT user-editable, NOT in DB — pattern generation is an internal English
# operation that powers the 0-ms regex tier (auto_entity rows). User-edited
# variants in any non-English language would silently break voice activation
# of radio stations / devices / scenes.
_PATTERN_SYSTEM_EN = (
    "You generate short English voice commands for a smart home system. "
    "Reply ONLY with a JSON array of lowercase ASCII strings. "
    "Each string is a 2-5 word English command. "
    "No explanations, no markdown, no non-ASCII characters."
)


class PatternGenerator:
    """Generates English-only intent_patterns rows from entity data in the DB."""

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

    # ── LLM pattern generation ─────────────────────────────────────────────

    async def _generate_patterns_via_llm(
        self, entity_type: str, name: str, context: str = "",
    ) -> list[str]:
        """Call LLM to generate 2-3 natural English voice command phrases.

        Returns list of English phrases (not regex). Empty list on failure.
        """
        prompts_by_type = {
            "radio_station": (
                f"Generate 2-3 short English voice commands to play the radio station \"{name}\".\n"
                f"Examples: \"play hit fm\", \"turn on bbc radio 1\"\n"
                f"{context}"
                f"Reply ONLY with a JSON array of lowercase strings, no extra text."
            ),
            "device": (
                f"Generate 2-3 short English voice commands to control the device \"{name}\".\n"
                f"Include both 'turn on' and 'turn off' variants.\n"
                f"{context}"
                f"Reply ONLY with a JSON array of lowercase strings, no extra text."
            ),
            "scene": (
                f"Generate 2-3 short English voice commands to activate the scene \"{name}\".\n"
                f"Examples: \"activate movie night\", \"run morning routine\"\n"
                f"{context}"
                f"Reply ONLY with a JSON array of lowercase strings, no extra text."
            ),
            "door_lock": (
                f"Generate 4 short English voice commands to lock and unlock "
                f"the smart lock named \"{name}\". Include 2 lock commands "
                f"AND 2 unlock commands. Examples: \"lock front door\", "
                f"\"unlock back door\", \"secure the garage\".\n"
                f"{context}"
                f"Reply ONLY with a JSON array of 4 lowercase strings, no extra text."
            ),
        }

        prompt = prompts_by_type.get(entity_type)
        if not prompt:
            return []

        try:
            from core.llm import llm_call

            raw = await llm_call(
                prompt,
                system=_PATTERN_SYSTEM_EN,  # hardcoded English, not user-editable
                json_mode=True,
                temperature=0.1,
                timeout=10.0,
            )
            if not raw:
                return []

            # Parse JSON array from response
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)

            start = cleaned.find("[")
            end = cleaned.rfind("]")
            if start == -1 or end == -1:
                return []

            phrases = json.loads(cleaned[start:end + 1])
            if not isinstance(phrases, list):
                return []

            # Filter, clean, and ASCII-gate (defence in depth — even if the
            # model leaks Cyrillic, the pattern never reaches the DB).
            result: list[str] = []
            for p in phrases[:MAX_PATTERNS_PER_ENTITY]:
                if not isinstance(p, str):
                    continue
                phrase = p.strip().lower()
                if len(phrase) < 3:
                    continue
                if not phrase.isascii():
                    logger.debug("Dropping non-ASCII pattern phrase: %r", phrase)
                    continue
                result.append(phrase)
            return result

        except Exception as exc:
            logger.debug("LLM pattern generation failed for %s '%s': %s", entity_type, name, exc)
            return []

    # ── Radio stations ──────────────────────────────────────────────────

    async def _gen_radio_station(self, session: AsyncSession, station_id: int) -> int:
        """Generate English patterns for one radio station."""
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

        # Use name_en for pattern generation (already translated by radio API)
        name = station.name_en or station.name_user
        if not name:
            return 0

        count = 0

        # Try LLM-generated patterns first
        llm_phrases = await self._generate_patterns_via_llm("radio_station", name)

        if llm_phrases:
            existing: list[str] = []
            for phrase in llm_phrases:
                regex = phrase_to_regex(phrase)
                if regex and validate_pattern(regex) and not deduplicate_pattern(regex, existing):
                    session.add(IntentPattern(
                        intent_id=idef.id, lang="en", pattern=regex,
                        source="auto_entity", entity_ref=ref,
                    ))
                    existing.append(regex)
                    count += 1
        else:
            # Fallback: hardcoded English template
            verbs_en = await self._get_vocab_words(session, "en", "verb", "play")
            name_esc = re.escape(name.lower())
            verb_alt = "|".join(re.escape(v) for v in verbs_en) if verbs_en else "play|put on|turn on"
            pattern = f"(?:{verb_alt})\\s+(?:radio\\s+)?(?:station\\s+)?{name_esc}"
            session.add(IntentPattern(
                intent_id=idef.id, lang="en", pattern=pattern,
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
        """Generate English patterns for one device."""
        from core.registry.models import Device, IntentPattern, IntentDefinition

        device = await session.get(Device, device_id)
        if not device:
            return 0

        ref = f"device:{device_id}"
        await session.execute(
            delete(IntentPattern).where(IntentPattern.entity_ref == ref)
        )

        # Prefer an explicit English name from meta.name_en (set via the
        # "Edit device" dialog in device-control settings). Falls back to
        # the display name — which may be Cyrillic or product code and
        # therefore useless for English voice commands.
        import json as _json
        meta_dict: dict = {}
        try:
            meta_dict = _json.loads(device.meta) if device.meta else {}
        except Exception:
            meta_dict = {}
        name_en = (meta_dict.get("name_en") or "").strip().lower()
        raw_name = name_en or device.name.lower()

        # Skip pattern generation if the name is not ASCII (e.g. only a
        # non-English display name is set). The ASCII guard in
        # phrase_to_regex would reject it anyway, but short-circuiting
        # here avoids creating empty intent_patterns rows.
        if not raw_name.isascii():
            logger.debug(
                "Skipping device %s: no ASCII name — set meta.name_en "
                "in the Edit dialog to enable voice patterns",
                device.device_id,
            )
            return 0
        name_esc = re.escape(raw_name)

        # Location suffix
        loc_part = ""
        if device.location:
            loc_esc = re.escape(device.location.lower())
            if loc_esc.isascii():
                loc_part = f"(?:\\s+(?:in|on)\\s+(?:the\\s+)?{loc_esc})?"

        entity_type = (device.entity_type or "").lower()
        count = 0

        # ── Door locks (Matter / Z-Wave) ─────────────────────────────────
        # Locks get device.lock / device.unlock instead of on/off — they
        # don't have a meaningful "power" state.
        if entity_type in ("lock", "door_lock"):
            idef_lock = await self._ensure_definition(
                session, "device.lock", "device-control", "DEVICE", "lock", 100,
                "Lock a smart lock",
            )
            idef_unlock = await self._ensure_definition(
                session, "device.unlock", "device-control", "DEVICE", "unlock", 100,
                "Unlock a smart lock",
            )

            llm_phrases = await self._generate_patterns_via_llm(
                "door_lock", raw_name,
            )

            lock_phrases: list[str] = []
            unlock_phrases: list[str] = []
            for phrase in llm_phrases:
                if "unlock" in phrase or "open " in phrase:
                    unlock_phrases.append(phrase)
                elif "lock" in phrase or "secure " in phrase or "shut " in phrase:
                    lock_phrases.append(phrase)

            if not lock_phrases:
                lock_phrases = [
                    f"lock {raw_name}",
                    f"lock the {raw_name}",
                    f"secure {raw_name}",
                ]
            if not unlock_phrases:
                unlock_phrases = [
                    f"unlock {raw_name}",
                    f"unlock the {raw_name}",
                    f"open {raw_name}",
                ]

            for phrase in lock_phrases:
                regex = phrase_to_regex(phrase)
                if regex and validate_pattern(regex):
                    session.add(IntentPattern(
                        intent_id=idef_lock.id, lang="en", pattern=regex,
                        source="auto_entity", entity_ref=ref,
                    ))
                    count += 1
            for phrase in unlock_phrases:
                regex = phrase_to_regex(phrase)
                if regex and validate_pattern(regex):
                    session.add(IntentPattern(
                        intent_id=idef_unlock.id, lang="en", pattern=regex,
                        source="auto_entity", entity_ref=ref,
                    ))
                    count += 1
            return count

        # ── Climate / thermostat — extra set_temperature pattern ─────────
        if entity_type in ("thermostat", "air_conditioner"):
            idef_temp = await self._ensure_definition(
                session, "device.set_temperature", "device-control", "CLIMATE", "set", 100,
                "Set the target temperature on a climate device",
            )
            pattern = (
                f"set\\s+(?:the\\s+)?{name_esc}\\s+(?:to\\s+)?"
                f"(?P<level>\\d{{1,2}})(?:\\s+degrees?)?"
            )
            session.add(IntentPattern(
                intent_id=idef_temp.id, lang="en", pattern=pattern,
                source="auto_entity", entity_ref=ref,
            ))
            count += 1
            # Climate devices also fall through to on/off below.

        # ── Default: device.on / device.off ──────────────────────────────
        verbs_on_en = await self._get_vocab_words(session, "en", "verb", "on")
        verbs_off_en = await self._get_vocab_words(session, "en", "verb", "off")

        # device.on — English only
        idef_on = await self._ensure_definition(
            session, "device.on", "", "DEVICE", "on", 100,
            "Turn on a device",
        )

        verb_alt = "|".join(re.escape(v) for v in verbs_on_en) if verbs_on_en else "turn on|switch on"
        pattern = f"(?:{verb_alt})\\s+(?:the\\s+)?{name_esc}{loc_part}"
        session.add(IntentPattern(
            intent_id=idef_on.id, lang="en", pattern=pattern,
            source="auto_entity", entity_ref=ref,
        ))
        count += 1

        # device.off — English only
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
        """Generate English patterns for one scene."""
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

        name = scene.name_en or scene.name_user
        if not name:
            return 0

        count = 0

        # Try LLM-generated patterns
        llm_phrases = await self._generate_patterns_via_llm("scene", name)

        if llm_phrases:
            existing: list[str] = []
            for phrase in llm_phrases:
                regex = phrase_to_regex(phrase)
                if regex and validate_pattern(regex) and not deduplicate_pattern(regex, existing):
                    session.add(IntentPattern(
                        intent_id=idef.id, lang="en", pattern=regex,
                        source="auto_entity", entity_ref=ref,
                    ))
                    existing.append(regex)
                    count += 1
        else:
            # Fallback: hardcoded English template
            name_esc = re.escape(name.lower())
            pattern = f"(?:activate|run|start|launch)\\s+(?:scene\\s+)?{name_esc}"
            session.add(IntentPattern(
                intent_id=idef.id, lang="en", pattern=pattern,
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
