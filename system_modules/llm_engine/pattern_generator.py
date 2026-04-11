"""
system_modules/llm_engine/pattern_generator.py — composite device-name index.

Historically this module generated regex patterns for every radio station,
scene and device added to the registry. With the move to LLM-only
classification (keyword-filtered catalog in ``intent_router``), pattern
generation is gone. What remains is a tiny in-memory index that
``device-control`` uses to resolve a ``name_en`` the LLM produced back to
a concrete ``device_id`` in O(1).

Public API:
  - ``get_device_id_by_name(name_en)`` — unique-name lookup, or ``None`` if
    the name is unknown or shared by 2+ devices (ambiguous).
  - ``is_ambiguous_name(name_en)`` — True if 2+ devices share the name.
  - ``rebuild()`` — rescan the ``devices`` table and refresh the index.
    Invoked by ``on_entity_changed`` after any device CRUD.
  - ``get_pattern_generator()`` — singleton accessor.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class PatternGenerator:
    """In-memory name → device_id index for the LLM's name_en param."""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory
        self._device_name_index: dict[str, str] = {}
        self._ambiguous_names: set[str] = set()

    # ── Public API ───────────────────────────────────────────────────────

    def get_device_id_by_name(self, name_en: str) -> str | None:
        """Return the device_id for a unique English name, or None."""
        if not name_en:
            return None
        return self._device_name_index.get(name_en.strip().lower())

    def is_ambiguous_name(self, name_en: str) -> bool:
        """True when ``name_en`` is shared by 2+ devices."""
        if not name_en:
            return False
        return name_en.strip().lower() in self._ambiguous_names

    async def rebuild(self) -> int:
        """Rescan ``devices`` → refresh the name_en index.

        Returns the number of devices indexed. Safe to call repeatedly;
        replaces the internal dicts atomically.
        """
        from sqlalchemy import select
        from core.registry.models import Device

        name_to_ids: dict[str, list[str]] = {}
        async with self._sf() as session:
            devices = list((await session.execute(select(Device))).scalars().all())
            for d in devices:
                try:
                    meta = json.loads(d.meta) if d.meta else {}
                except Exception:
                    meta = {}
                name = (meta.get("name_en") or d.name or "").strip().lower()
                if not name:
                    continue
                name_to_ids.setdefault(name, []).append(str(d.device_id))

        uniques: dict[str, str] = {}
        ambiguous: set[str] = set()
        for name, ids in name_to_ids.items():
            if len(ids) == 1:
                uniques[name] = ids[0]
            else:
                ambiguous.add(name)

        self._device_name_index = uniques
        self._ambiguous_names = ambiguous
        logger.info(
            "PatternGenerator.rebuild: %d devices → %d unique names, %d ambiguous",
            len(name_to_ids), len(uniques), len(ambiguous),
        )
        return len(name_to_ids)

    # ── Deprecated shims ─────────────────────────────────────────────────
    #
    # The old PatternGenerator wrote regex patterns into intent_patterns
    # for every entity CRUD. The LLM-only router no longer reads that
    # table, so all of these are no-ops. They stay as stubs only so that
    # legacy callers (core/api/helpers.py, media_player) don't explode on
    # import — new code must NOT call them.

    async def generate_for_entity(
        self, entity_type: str, entity_id: int | str,  # noqa: ARG002
    ) -> int:
        if entity_type == "device":
            return await self.rebuild()
        return 0

    async def delete_for_entity(
        self, entity_type: str, entity_id: int | str,  # noqa: ARG002
    ) -> int:
        if entity_type == "device":
            return await self.rebuild()
        return 0

    async def rebuild_composite_device_patterns(self) -> int:
        return await self.rebuild()

    async def regenerate_all(self, entity_type: str | None = None) -> int:  # noqa: ARG002
        return await self.rebuild()


# ── Singleton ────────────────────────────────────────────────────────────

_generator: PatternGenerator | None = None


def get_pattern_generator() -> PatternGenerator:
    global _generator
    if _generator is None:
        from core.module_loader.sandbox import get_sandbox
        sf = get_sandbox()._session_factory
        _generator = PatternGenerator(sf)
    return _generator
