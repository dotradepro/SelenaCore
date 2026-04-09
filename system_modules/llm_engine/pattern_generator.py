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
        # In-memory lookup populated by rebuild_composite_device_patterns().
        # Maps lowercased name_en → device_id, used by device-control to
        # resolve composite-pattern matches in O(1) without re-querying
        # the DB on every voice command.
        self._device_name_index: dict[str, str] = {}

    async def generate_for_entity(
        self, entity_type: str, entity_id: int | str,
    ) -> int:
        """Generate patterns for a specific entity. Returns count of patterns created.

        For ``entity_type='device'`` we always rebuild the composite
        on/off patterns from scratch — they cover the whole registry in
        exactly TWO rows so per-id work is wasted. Radio stations and
        scenes still go through the per-entity path.
        """
        if entity_type == "device":
            return await self.rebuild_composite_device_patterns()
        async with self._sf() as session:
            async with session.begin():
                if entity_type == "radio_station":
                    return await self._gen_radio_station(session, int(entity_id))
                elif entity_type == "scene":
                    return await self._gen_scene(session, int(entity_id))
        return 0

    async def delete_for_entity(
        self, entity_type: str, entity_id: int | str,
    ) -> int:
        """Delete auto-generated patterns for an entity. Returns count deleted.

        For devices, the composite pattern path means a single removal
        triggers a full rebuild so the alternation drops the gone device.
        """
        if entity_type == "device":
            return await self.rebuild_composite_device_patterns()

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

    # ── Composite device patterns ──────────────────────────────────────

    async def rebuild_composite_device_patterns(self) -> int:
        """Build ONE composite regex per device verb that covers every device.

        Replaces the old N×2 row-per-device approach. After this runs the
        ``intent_patterns`` table holds at most TWO ``auto_entity`` rows
        with ``entity_ref='device:composite'`` for the whole registry —
        one for ``device.on`` and one for ``device.off``. The matched
        ``name`` named-group is later resolved to a concrete device_id
        via :meth:`get_device_id_by_name`.

        Climate / lock devices keep their per-device patterns until a
        future iteration — they have additional named groups (level,
        mode, fan_speed) that don't compress into a single composite
        cleanly without combinatorial explosion.
        """
        from core.registry.models import Device, IntentPattern
        import json as _json

        async with self._sf() as session:
            async with session.begin():
                # Wipe ALL device on/off rows (composite + any leftover
                # legacy per-device rows from before this refactor) so
                # we never accumulate stale alternations.
                await session.execute(
                    delete(IntentPattern).where(
                        IntentPattern.source == "auto_entity",
                        IntentPattern.entity_ref.like("device:%"),
                    )
                )

                devices = list((await session.execute(select(Device))).scalars())
                names_all_esc: list[str] = []
                names_climate_esc: list[str] = []
                names_lock_esc: list[str] = []
                locations_esc: set[str] = set()
                index: dict[str, str] = {}

                for d in devices:
                    try:
                        meta = _json.loads(d.meta) if d.meta else {}
                    except Exception:
                        meta = {}
                    name_en = (meta.get("name_en") or "").strip().lower()
                    if not name_en or not name_en.isascii():
                        continue
                    name_esc = re.escape(name_en)
                    names_all_esc.append(name_esc)
                    index[name_en] = d.device_id

                    etype = (d.entity_type or "").lower()
                    if etype in ("thermostat", "air_conditioner"):
                        names_climate_esc.append(name_esc)
                    if etype in ("lock", "door_lock"):
                        names_lock_esc.append(name_esc)

                    loc_en = (meta.get("location_en") or "").strip().lower()
                    if not loc_en and d.location and d.location.isascii():
                        loc_en = d.location.lower()
                    if loc_en:
                        locations_esc.add(re.escape(loc_en))

                # Atomic update of the in-memory index — match() never
                # walks this so a brief inconsistency window is fine.
                self._device_name_index = index

                if not names_all_esc:
                    logger.info(
                        "Composite device patterns: no devices with name_en — skipping",
                    )
                    return 0

                def _alt(items: list[str]) -> str:
                    """Sort longest-first so multi-word names win over their
                    prefixes during regex matching ("air conditioner" vs "air")."""
                    return "|".join(sorted(set(items), key=len, reverse=True))

                name_alt = _alt(names_all_esc)
                loc_part = ""
                if locations_esc:
                    loc_alt = _alt(list(locations_esc))
                    loc_part = (
                        f"(?:\\s+(?:in|on)\\s+(?:the\\s+)?(?P<location>{loc_alt}))?"
                    )

                # ── device.on / device.off (every device) ──
                idef_on = await self._ensure_definition(
                    session, "device.on", "device-control", "DEVICE", "on", 100,
                    "Turn a device on",
                )
                idef_off = await self._ensure_definition(
                    session, "device.off", "device-control", "DEVICE", "off", 100,
                    "Turn a device off",
                )
                session.add(IntentPattern(
                    intent_id=idef_on.id, lang="en",
                    pattern=(
                        f"^(?:turn\\s+on|switch\\s+on|enable)"
                        f"\\s+(?:the\\s+)?(?P<name>{name_alt}){loc_part}\\s*\\??$"
                    ),
                    source="auto_entity", entity_ref="device:composite",
                ))
                session.add(IntentPattern(
                    intent_id=idef_off.id, lang="en",
                    pattern=(
                        f"^(?:turn\\s+off|switch\\s+off|disable)"
                        f"\\s+(?:the\\s+)?(?P<name>{name_alt}){loc_part}\\s*\\??$"
                    ),
                    source="auto_entity", entity_ref="device:composite",
                ))
                count = 2

                # ── device.set_temperature (climate devices only) ──
                if names_climate_esc:
                    climate_alt = _alt(names_climate_esc)
                    idef_temp = await self._ensure_definition(
                        session, "device.set_temperature", "device-control",
                        "CLIMATE", "set", 100,
                        "Set the target temperature on a climate device",
                    )
                    session.add(IntentPattern(
                        intent_id=idef_temp.id, lang="en",
                        pattern=(
                            f"^set\\s+(?:the\\s+)?(?P<name>{climate_alt})"
                            f"\\s+(?:to\\s+)?(?P<level>\\d{{1,2}})"
                            f"(?:\\s+degrees?)?{loc_part}\\s*\\??$"
                        ),
                        source="auto_entity", entity_ref="device:composite",
                    ))
                    count += 1

                # ── device.lock / device.unlock (locks only) ──
                if names_lock_esc:
                    lock_alt = _alt(names_lock_esc)
                    idef_lock = await self._ensure_definition(
                        session, "device.lock", "device-control",
                        "DEVICE", "lock", 100, "Lock a smart lock",
                    )
                    idef_unlock = await self._ensure_definition(
                        session, "device.unlock", "device-control",
                        "DEVICE", "unlock", 100, "Unlock a smart lock",
                    )
                    session.add(IntentPattern(
                        intent_id=idef_lock.id, lang="en",
                        pattern=(
                            f"^(?:lock|secure|shut)\\s+(?:the\\s+)?"
                            f"(?P<name>{lock_alt})(?:\\s+door)?{loc_part}\\s*\\??$"
                        ),
                        source="auto_entity", entity_ref="device:composite",
                    ))
                    session.add(IntentPattern(
                        intent_id=idef_unlock.id, lang="en",
                        pattern=(
                            f"^(?:unlock|open)\\s+(?:the\\s+)?"
                            f"(?P<name>{lock_alt})(?:\\s+door)?{loc_part}\\s*\\??$"
                        ),
                        source="auto_entity", entity_ref="device:composite",
                    ))
                    count += 2

        logger.info(
            "Composite device patterns rebuilt: %d devices (%d climate, %d lock), "
            "%d rooms, %d patterns",
            len(index), len(names_climate_esc), len(names_lock_esc),
            len(locations_esc), count,
        )
        return count

    def get_device_id_by_name(self, name_en: str) -> str | None:
        """O(1) lookup used by device-control to resolve composite matches.

        Returns the device_id whose ``meta.name_en`` equals ``name_en``
        (case-insensitive), or ``None`` if no such device exists.
        """
        if not name_en:
            return None
        return self._device_name_index.get(name_en.strip().lower())

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

    # ── Devices: see rebuild_composite_device_patterns() above ───────────
    # The legacy per-device generator was removed in favour of composite
    # patterns that scale O(1) in DB rows regardless of registry size.

    async def _regenerate_devices(self) -> int:
        """Regenerate device patterns via the composite rebuild path.

        One call covers the entire registry — there is no per-id work
        anymore. Returns the number of pattern rows inserted (0 or 2).
        """
        return await self.rebuild_composite_device_patterns()

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
