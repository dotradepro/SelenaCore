"""
system_modules/llm_engine/intent_compiler.py — intent definition registry.

Post-refactor this module is a thin read-only view over ``intent_definitions``
rows. It no longer compiles any regex patterns: the whole FastMatcher / Tier-0
path was removed when the router moved to LLM-only classification with a
keyword-filtered catalog (see ``intent_router._build_filtered_catalog``).

The surviving public surface is:
  - ``get_all_intents()`` — every enabled definition, for the LLM catalog
  - ``get_definition(name)`` — single lookup, used by voice-core to decide
    whether an intent is owned by a system module
  - ``full_reload()`` — refresh the in-memory cache from DB after CRUD
  - ``set_session_factory()`` / ``get_intent_compiler()`` — wiring

Anything that used to deal with compiled regex (``_flat_en``, ``_buckets_en``,
``_VERB_BUCKETS``, ``_pattern_specificity``, ``match()``, ``_PatternEntry``)
has been deleted. If you find a dangling caller, point it at the keyword
filter in ``intent_router`` instead.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CompiledIntent:
    """Lightweight view of an ``intent_definitions`` row.

    The ``patterns`` field is kept (always empty) so callers that still
    destructure the dataclass don't crash during the transition.
    """
    intent: str
    module: str
    noun_class: str
    verb: str
    priority: int
    description: str
    patterns: dict[str, list[Any]] = field(default_factory=dict)
    params_schema: dict = field(default_factory=dict)
    entity_types: list[str] = field(default_factory=list)


class IntentCompiler:
    """In-memory cache of intent definitions loaded from SQLite."""

    def __init__(self, session_factory=None) -> None:
        self._sf = session_factory
        self._compiled: list[CompiledIntent] = []
        self._lock = threading.Lock()
        self._version: int = 0
        self._loaded = False

    def set_session_factory(self, sf) -> None:
        self._sf = sf

    # ── Public API ───────────────────────────────────────────────────────

    def load(self, languages: list[str] | None = None) -> None:  # noqa: ARG002
        """Synchronous load for startup compatibility.

        Spins a throwaway event loop on a worker thread so the call can
        be made from sync contexts (e.g. FastAPI startup hooks that have
        not yet started the main event loop).
        """
        if self._sf is None:
            logger.warning("IntentCompiler: no session_factory — cannot load from DB")
            return

        def _sync_load() -> None:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._async_load())
            finally:
                loop.close()

        t = threading.Thread(target=_sync_load, daemon=True)
        t.start()
        t.join(timeout=5.0)

        if self._loaded:
            logger.info(
                "IntentCompiler: loaded %d intents from DB (v%d)",
                len(self._compiled), self._version,
            )

    async def async_load(self) -> None:
        await self._async_load()

    async def full_reload(self) -> None:
        if self._sf is None:
            return
        await self._async_load()
        logger.info(
            "IntentCompiler: full reload (v%d, %d intents)",
            self._version, len(self._compiled),
        )

    async def reload_intent(self, intent_name: str) -> None:  # noqa: ARG002
        """Granular hot-reload: same as full_reload in the new design."""
        if self._sf is None:
            return
        await self._async_load()

    # ── Read-only accessors ──────────────────────────────────────────────

    def get_all_intents(self) -> list[CompiledIntent]:
        if not self._loaded:
            self.load()
        return list(self._compiled)

    def get_definition(self, intent_name: str) -> CompiledIntent | None:
        if not self._loaded:
            self.load()
        for c in self._compiled:
            if c.intent == intent_name:
                return c
        return None

    def get_all_modules(self) -> list[str]:
        if not self._loaded:
            self.load()
        return list({c.module for c in self._compiled if c.module})

    def get_all_noun_classes(self) -> list[str]:
        if not self._loaded:
            self.load()
        return list({c.noun_class for c in self._compiled if c.noun_class})

    def get_intents_for_noun_class(self, noun_class: str) -> list[str]:
        if not self._loaded:
            self.load()
        return [c.intent for c in self._compiled if c.noun_class == noun_class]

    # ── Internal ─────────────────────────────────────────────────────────

    async def _async_load(self) -> None:
        from sqlalchemy import select
        from core.registry.models import IntentDefinition

        async with self._sf() as session:
            result = await session.execute(
                select(IntentDefinition).where(
                    IntentDefinition.enabled == True  # noqa: E712
                )
            )
            definitions = list(result.scalars().all())

        new_compiled: list[CompiledIntent] = []
        for defn in definitions:
            new_compiled.append(CompiledIntent(
                intent=defn.intent,
                module=defn.module,
                noun_class=defn.noun_class,
                verb=defn.verb,
                priority=defn.priority,
                description=defn.description,
                patterns={},
                params_schema=defn.get_params_schema(),
                entity_types=defn.get_entity_types(),
            ))
        new_compiled.sort(key=lambda c: c.priority, reverse=True)

        with self._lock:
            self._compiled = new_compiled
            self._version += 1
            self._loaded = True


# ── Singleton ────────────────────────────────────────────────────────────

_compiler: IntentCompiler | None = None


def get_intent_compiler() -> IntentCompiler:
    global _compiler
    if _compiler is None:
        _compiler = IntentCompiler()

    # Lazy init: acquire the session factory from the running sandbox on
    # first touch so callers outside the core lifespan still work.
    if not _compiler._loaded and _compiler._sf is None:
        try:
            from core.module_loader.sandbox import get_sandbox
            sf = get_sandbox()._session_factory
            if sf:
                _compiler.set_session_factory(sf)
                _compiler.load()
        except Exception:
            logger.debug("IntentCompiler: deferred load (session_factory not ready)")
    return _compiler
