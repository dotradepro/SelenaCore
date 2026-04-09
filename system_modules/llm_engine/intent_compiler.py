"""
system_modules/llm_engine/intent_compiler.py — DB-driven intent pattern compiler.

Reads intent_definitions + intent_patterns + intent_vocab from SQLite DB.
Compiles regex patterns into in-memory cache for 0ms matching.

Supports hot-reload: when data changes, affected intents are recompiled
without restarting the server.

Public API (same as before — drop-in replacement):
  load()                    — query DB → compile → cache
  match(text, lang)         — iterate compiled, return first hit
  get_intents_for_module()  — filter by module
  reload_intent(intent)     — granular hot-reload
  full_reload()             — rebuild all
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _pattern_specificity(pattern: str) -> int:
    """Higher score = more specific = higher match priority within a tier.

    Used as a tiebreaker so that within a single priority bucket, a
    pattern with named groups + anchors always wins over a loose
    parameterless one (e.g. ``set\\s+(?P<x>\\d+)$`` beats ``set\\s+\\d+``).

    Heuristics (additive):
      + raw length (longer literal = more specific)
      + 50 per named group  ``(?P<name>...)``
      + 30 for end anchor   ``$`` / ``\\Z``
      + 30 for start anchor ``^`` / ``\\A``
      + 20 per word boundary ``\\b``
      + 10 per non-capturing group ``(?:...)``
      - 5  per ``.*`` / ``.+`` (greedy wildcard reduces specificity)
    """
    score = len(pattern)
    score += pattern.count("(?P<") * 50
    if pattern.endswith("$") or pattern.endswith("\\Z"):
        score += 30
    if pattern.startswith("^") or pattern.startswith("\\A"):
        score += 30
    score += pattern.count("\\b") * 20
    score += pattern.count("(?:") * 10
    score -= pattern.count(".*") * 5
    score -= pattern.count(".+") * 5
    return score


@dataclass
class _PatternEntry:
    """Single compiled pattern with optional entity reference."""
    regex: re.Pattern
    entity_ref: str | None = None  # e.g. "radio_station:42"
    specificity: int = 0           # higher = more specific (see _pattern_specificity)


@dataclass
class CompiledIntent:
    """In-memory compiled intent with regex patterns."""
    intent: str
    module: str
    noun_class: str
    verb: str
    priority: int
    description: str
    patterns: dict[str, list[_PatternEntry]]  # lang → compiled pattern entries
    params_schema: dict = field(default_factory=dict)


# Keep SystemIntentEntry for backward compatibility with IntentRouter
@dataclass
class SystemIntentEntry:
    """In-process intent registration for SYSTEM modules.

    .. note::
        Patterns are **English-only** by design. The ``patterns`` dict only
        the ``"en"`` key is honoured by the IntentCompiler / IntentRouter.
        Non-English speech is expected to fall through to the LLM tier
        (Tier 3), which classifies any language and returns an English
        intent name. Other language keys may exist for legacy reasons but
        are silently ignored at match time.
    """
    module: str
    intent: str
    patterns: dict[str, list[str]]  # only patterns["en"] is consulted
    description: str = ""
    priority: int = 0

    def en_patterns(self) -> list[str]:
        """Return the English pattern list, warning if other langs are present."""
        extra = [k for k in self.patterns.keys() if k != "en"]
        if extra:
            logger.warning(
                "SystemIntentEntry %s/%s has non-en pattern keys %s — ignored "
                "(English-only matching, see LLM fallback)",
                self.module, self.intent, extra,
            )
        return self.patterns.get("en", [])


class IntentCompiler:
    """DB-driven intent pattern compiler with in-memory regex cache."""

    def __init__(self, session_factory=None) -> None:
        self._sf = session_factory
        self._compiled: list[CompiledIntent] = []
        self._lock = threading.Lock()
        self._version: int = 0
        self._loaded = False

    def set_session_factory(self, sf) -> None:
        """Inject session factory (called from main.py before load)."""
        self._sf = sf

    # ── Public API ───────────────────────────────────────────────────────

    def load(self, languages: list[str] | None = None) -> None:
        """Load intents from DB synchronously (for startup compatibility).

        Creates a new event loop in a thread to run the async query.
        """
        if self._sf is None:
            logger.warning("IntentCompiler: no session_factory — cannot load from DB")
            return

        def _sync_load():
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
        """Load intents from DB (async version, for hot-reload)."""
        await self._async_load()

    def match(self, text: str, lang: str = "en") -> dict[str, Any] | None:
        """Match text against compiled English patterns. Returns dict or None.

        All patterns are English-only. Non-English input naturally falls through
        to LLM tier which handles translation and returns English patterns.

        Thread-safe: reads a snapshot reference of _compiled.
        """
        if not self._loaded:
            self.load()

        text_lower = text.lower().strip()
        # Walk the precomputed flat list so two patterns at the same
        # priority are ordered by specificity (more anchors / named
        # groups → matches first). See _async_load() for how flat_en is
        # built. Falls back to legacy iteration if a stale instance from
        # before the flatten refactor sneaks in.
        flat = getattr(self, "_flat_en", None)
        if flat is None:
            flat = []
            for ci in self._compiled:
                for pe in ci.patterns.get("en", []):
                    flat.append((ci.priority, pe.specificity, ci, pe))

        for _prio, _spec, entry, pe in flat:
            m = pe.regex.search(text_lower)
            if m:
                params = {k: v for k, v in m.groupdict().items() if v is not None}
                result: dict[str, Any] = {
                    "intent": entry.intent,
                    "module": entry.module,
                    "noun_class": entry.noun_class,
                    "verb": entry.verb,
                    "params": params,
                    "source": "system_module",
                }
                if pe.entity_ref:
                    result["entity_ref"] = pe.entity_ref
                return result

        return None

    def get_intents_for_module(self, module_name: str) -> list[SystemIntentEntry]:
        """Return SystemIntentEntry list for a specific module (backward compat)."""
        if not self._loaded:
            self.load()
        result = []
        for c in self._compiled:
            if c.module == module_name:
                # Convert compiled patterns back to string patterns
                str_patterns: dict[str, list[str]] = {}
                for lang, pats in c.patterns.items():
                    str_patterns[lang] = [pe.regex.pattern for pe in pats]
                result.append(SystemIntentEntry(
                    module=c.module,
                    intent=c.intent,
                    patterns=str_patterns,
                    description=c.description,
                    priority=c.priority,
                ))
        return result

    def get_all_modules(self) -> list[str]:
        """Return list of all module names with intents."""
        if not self._loaded:
            self.load()
        return list({c.module for c in self._compiled if c.module})

    def get_all_intents(self) -> list[CompiledIntent]:
        """Return all compiled intents."""
        if not self._loaded:
            self.load()
        return list(self._compiled)

    async def reload_intent(self, intent_name: str) -> None:
        """Granular hot-reload: recompile one intent from DB."""
        if self._sf is None:
            return
        await self._async_load()
        logger.info("IntentCompiler: reloaded intent '%s' (full rebuild, v%d)", intent_name, self._version)

    async def full_reload(self) -> None:
        """Full rebuild from DB."""
        if self._sf is None:
            return
        await self._async_load()
        logger.info("IntentCompiler: full reload (v%d, %d intents)", self._version, len(self._compiled))

    # ── Internal ─────────────────────────────────────────────────────────

    async def _async_load(self) -> None:
        """Query DB and compile all patterns into in-memory cache."""
        from sqlalchemy import select
        from core.registry.models import IntentDefinition, IntentPattern

        async with self._sf() as session:
            # Load all enabled definitions with their patterns
            result = await session.execute(
                select(IntentDefinition).where(IntentDefinition.enabled == True)
            )
            definitions = list(result.scalars().all())

            # Load all patterns
            result = await session.execute(select(IntentPattern))
            all_patterns = list(result.scalars().all())

        # Group patterns by intent_id: {id: {lang: [(pattern_str, entity_ref), ...]}}
        # English-only by design — non-en rows are skipped at load time so the
        # in-memory cache stays uniform with the runtime contract.
        patterns_by_id: dict[int, dict[str, list[tuple[str, str | None]]]] = {}
        for p in all_patterns:
            if p.lang != "en":
                logger.debug(
                    "IntentCompiler: skipping non-en pattern (intent_id=%s lang=%s)",
                    p.intent_id, p.lang,
                )
                continue
            patterns_by_id.setdefault(p.intent_id, {}).setdefault(p.lang, []).append(
                (p.pattern, p.entity_ref)
            )

        # Compile
        new_compiled: list[CompiledIntent] = []
        for defn in definitions:
            lang_patterns = patterns_by_id.get(defn.id, {})
            compiled_patterns: dict[str, list[_PatternEntry]] = {}

            for lang, pattern_tuples in lang_patterns.items():
                compiled_list: list[_PatternEntry] = []
                for ps, eref in pattern_tuples:
                    try:
                        compiled_list.append(_PatternEntry(
                            regex=re.compile(ps, re.IGNORECASE),
                            entity_ref=eref,
                            specificity=_pattern_specificity(ps),
                        ))
                    except re.error as exc:
                        logger.warning(
                            "IntentCompiler: bad regex '%s' for %s/%s: %s",
                            ps, defn.intent, lang, exc,
                        )
                if compiled_list:
                    compiled_patterns[lang] = compiled_list

            # Include the intent in the in-memory catalogue even if it
            # has zero compiled patterns. Pattern-less intents are
            # invisible to the FastMatcher (no entry in flat_en) but
            # MUST appear in get_all_intents() so the LLM tier sees
            # them in its dynamic catalog and can classify natural
            # language to them. This is the path for "hard intents
            # declared by a module" that rely entirely on LLM-driven
            # routing instead of regex shortcuts.
            new_compiled.append(CompiledIntent(
                intent=defn.intent,
                module=defn.module,
                noun_class=defn.noun_class,
                verb=defn.verb,
                priority=defn.priority,
                description=defn.description,
                patterns=compiled_patterns,
                params_schema=defn.get_params_schema(),
            ))

        # Sort by priority descending
        new_compiled.sort(key=lambda c: c.priority, reverse=True)

        # Flatten ALL English patterns into a single list sorted by
        # (priority DESC, specificity DESC). match() walks this list so
        # that within a single priority bucket the more-specific pattern
        # always wins, regardless of which intent owns it. This is the
        # deterministic conflict-resolution rule for overlapping patterns
        # like "(?:current\\s+)?temperature$" vs
        # "(?:current\\s+)?temperature\\s+in\\s+(?P<location>...)$".
        flat_en: list[tuple[int, int, CompiledIntent, _PatternEntry]] = []
        for ci in new_compiled:
            for pe in ci.patterns.get("en", []):
                flat_en.append((ci.priority, pe.specificity, ci, pe))
        flat_en.sort(key=lambda t: (-t[0], -t[1]))

        # Atomic swap
        with self._lock:
            self._compiled = new_compiled
            self._flat_en = flat_en
            self._version += 1
            self._loaded = True

    # ── Backward compat helpers ──────────────────────────────────────────

    def get_all_noun_classes(self) -> list[str]:
        if not self._loaded:
            self.load()
        return list({c.noun_class for c in self._compiled if c.noun_class})

    def get_intents_for_noun_class(self, noun_class: str) -> list[str]:
        if not self._loaded:
            self.load()
        return [c.intent for c in self._compiled if c.noun_class == noun_class]

    def get_definition(self, intent_name: str) -> CompiledIntent | None:
        for c in self._compiled:
            if c.intent == intent_name:
                return c
        return None


# ── Singleton ────────────────────────────────────────────────────────────

_compiler: IntentCompiler | None = None


def get_intent_compiler() -> IntentCompiler:
    global _compiler
    if _compiler is None:
        _compiler = IntentCompiler()

    # Lazy init: try to acquire session_factory if not loaded yet
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
