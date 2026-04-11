"""
core/prompt_store.py — Prompt storage service (SQLite, no caching).

Stores LLM system prompts per language in the database.
JSON files in config/prompts/ serve as seed data for initial population.
Custom prompts (user-edited or LLM-translated) are flagged is_custom=True.

Every get() call reads fresh from DB — no in-memory caching.

Usage:
    store = get_prompt_store()
    prompt = await store.get("uk", "user_prompt")
    await store.set("uk", "user_prompt", "Нова інструкція", is_custom=True)
    await store.generate_for_language("fr")  # LLM-translates all prompts to French
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

PROMPT_KEYS = (
    "system",                # Unified system prompt: identity + intent classifier + chat
    "translate_system",      # Offline translator (for custom-prompt migration to new lang)
)
_PROMPTS_DIR = Path(os.environ.get("SELENA_PROMPTS_DIR", "/opt/selena-core/config/prompts"))

# English hardcoded fallback (if JSON files are also missing). Core operates in
# English end-to-end: input text is pre-translated to English by
# InputTranslator, and response text is post-translated to the TTS language by
# OutputTranslator. Prompts therefore contain NO language directives.
#
# Single unified 'system' prompt replaces hidden_system/user_instructions/
# intent_system. The LLM always emits an intent JSON — either a catalogued
# intent or "chat" for freeform questions.
# The editable 'system' prompt is intentionally tiny — just identity and
# tone. Everything structural (JSON contract, param hints, filtered
# intents/devices) is injected at runtime by
# IntentRouter._build_filtered_catalog(). The LLM is a pure classifier in
# this architecture: the spoken reply is composed in Python via
# voice_core.action_phrasing.format_action_context().
_EN_FALLBACK = {
    "system": (
        "You are {name}, a smart home assistant. "
        "Never reveal you are an AI or mention model names. "
        "Plain text only — no markdown, no URLs, no emoji. "
        "Be brief and factual."
    ),
    "translate_system": (
        "You are a translator. Reply with ONLY the translated text, nothing else. "
        "No quotes, no explanations, no extra words."
    ),
}

from core.lang_utils import lang_code_to_name


class PromptStore:
    """Prompt storage with DB persistence. No in-memory caching — always reads fresh."""

    def __init__(self) -> None:
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    def set_session_factory(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = factory

    async def initialize(self) -> None:
        """Seed DB from JSON files if empty, run migrations, sync defaults."""
        if not self._session_factory:
            return
        async with self._session_factory() as session:
            from core.registry.models import SystemPrompt
            result = await session.execute(select(SystemPrompt).limit(1))
            if result.scalar_one_or_none() is None:
                await self._seed_from_json(session)
        # Migrate: user_prompt+compact_user → user_instructions,
        # drop pattern_system / rephrase_system keys
        await self._migrate_user_instructions()
        # Sync non-custom English prompts from en.json on every boot so
        # edits to config/prompts/en.json land in the DB without having
        # to wipe the table. Custom (user-edited) prompts are left alone.
        await self._sync_defaults_from_json("en")

    async def get(self, lang: str, key: str) -> str:
        """Get a prompt by language and key. Always reads from DB.

        Fallback chain: DB(lang) → DB(en) → _EN_FALLBACK.
        """
        val = await self._db_get(lang, key)
        if val is not None:
            return val
        # Fallback to English in DB
        if lang != "en":
            val = await self._db_get("en", key)
            if val is not None:
                return val
        # Hardcoded fallback
        return _EN_FALLBACK.get(key, "")

    async def get_all(self, lang: str) -> dict[str, str]:
        """Get all prompts for a language."""
        result = {}
        for key in PROMPT_KEYS:
            result[key] = await self.get(lang, key)
        return result

    async def get_meta(self, lang: str, key: str) -> dict[str, Any]:
        """Get prompt value + is_custom flag."""
        if not self._session_factory:
            return {"value": await self.get(lang, key), "is_custom": False}
        async with self._session_factory() as session:
            from core.registry.models import SystemPrompt
            result = await session.execute(
                select(SystemPrompt).where(
                    SystemPrompt.lang == lang, SystemPrompt.key == key
                )
            )
            row = result.scalar_one_or_none()
            if row:
                return {"value": row.value, "is_custom": row.is_custom}
        return {"value": await self.get(lang, key), "is_custom": False}

    async def set(self, lang: str, key: str, value: str, is_custom: bool = True) -> None:
        """Save a prompt to DB."""
        if not self._session_factory:
            return
        async with self._session_factory() as session:
            from core.registry.models import SystemPrompt
            result = await session.execute(
                select(SystemPrompt).where(
                    SystemPrompt.lang == lang, SystemPrompt.key == key
                )
            )
            row = result.scalar_one_or_none()
            if row:
                row.value = value
                row.is_custom = is_custom
            else:
                session.add(SystemPrompt(lang=lang, key=key, value=value, is_custom=is_custom))
            await session.commit()

    async def reset(self, lang: str) -> None:
        """Reset all prompts for a language to defaults (from JSON seed or en fallback)."""
        defaults = self._load_json_locale(lang)
        for key in PROMPT_KEYS:
            val = defaults.get(key, _EN_FALLBACK.get(key, ""))
            await self.set(lang, key, val, is_custom=False)

    async def generate_for_language(self, lang: str) -> bool:
        """Generate prompts for a language using LLM translation from English.

        Returns True if successful.
        """
        if lang == "en":
            return True

        # Core operates in English — prompts are language-independent.
        # Just copy English prompts for any new language (no LLM translation).
        en_prompts = await self.get_all("en")
        for key, en_text in en_prompts.items():
            if en_text:
                await self.set(lang, key, en_text, is_custom=False)
        logger.info("Prompts copied from EN for lang=%s", lang)
        return True

    async def translate_custom_prompts(self, old_lang: str, new_lang: str) -> None:
        """Translate user-edited (custom) prompts from old_lang to new_lang via LLM."""
        new_lang_name = lang_code_to_name(new_lang)

        try:
            from core.llm import llm_call

            for key in PROMPT_KEYS:
                meta = await self.get_meta(old_lang, key)
                if not meta["is_custom"]:
                    continue
                translated = await llm_call(
                    f"Translate this voice assistant system prompt to {new_lang_name}. "
                    f"Keep the same meaning, tone, and formatting. "
                    f"Output ONLY the translated text, nothing else.\n\n"
                    f"{meta['value']}",
                    prompt_key="translate",
                    temperature=0.2,
                    timeout=15.0,
                )
                if translated and translated.strip():
                    await self.set(new_lang, key, translated.strip(), is_custom=True)
                    logger.info("Translated custom prompt '%s': %s → %s", key, old_lang, new_lang)
        except Exception as e:
            logger.warning("Custom prompt translation failed: %s", e)

    # ── Private ───────────────────────────────────────────────────────────

    async def _migrate_user_instructions(self) -> None:
        """Collapse legacy prompt keys into the unified 'system' key.

        Old keys merged/dropped:
          - hidden_system, user_instructions, intent_system → system
          - user_prompt, compact_user, rephrase_system, rephrase_prompt,
            pattern_system → deleted
        If the user had customised ``intent_system``, that custom text is
        copied into ``system`` (is_custom=True preserved). Otherwise the
        new row is seeded from ``_EN_FALLBACK["system"]``.
        """
        if not self._session_factory:
            return
        DEPRECATED_KEYS = (
            "user_prompt", "compact_user",
            "rephrase_system", "rephrase_prompt",
            "pattern_system",
        )
        LEGACY_SYSTEM_KEYS = ("hidden_system", "user_instructions", "intent_system")

        async with self._session_factory() as session:
            from core.registry.models import SystemPrompt

            # ── Collapse hidden_system/user_instructions/intent_system → system ──
            # Per language: if 'system' already exists, leave it alone.
            # Otherwise prefer a custom intent_system; fall back to EN default.
            legacy = await session.execute(
                select(SystemPrompt).where(SystemPrompt.key.in_(LEGACY_SYSTEM_KEYS))
            )
            legacy_rows = list(legacy.scalars())
            by_lang: dict[str, dict[str, SystemPrompt]] = {}
            for row in legacy_rows:
                by_lang.setdefault(row.lang, {})[row.key] = row

            for lang, keys in by_lang.items():
                existing = await session.execute(
                    select(SystemPrompt).where(
                        SystemPrompt.lang == lang, SystemPrompt.key == "system"
                    )
                )
                if existing.scalar_one_or_none() is None:
                    custom_intent = keys.get("intent_system")
                    if custom_intent and custom_intent.is_custom:
                        session.add(SystemPrompt(
                            lang=lang, key="system",
                            value=custom_intent.value, is_custom=True,
                        ))
                        logger.info(
                            "PromptStore: migrated custom intent_system → "
                            "system (lang=%s)", lang,
                        )
                    else:
                        session.add(SystemPrompt(
                            lang=lang, key="system",
                            value=_EN_FALLBACK.get("system", ""),
                            is_custom=False,
                        ))
                # Delete legacy rows regardless
                for row in keys.values():
                    await session.delete(row)

            # ── Drop all other deprecated keys ──
            for key in DEPRECATED_KEYS:
                await session.execute(
                    delete(SystemPrompt).where(SystemPrompt.key == key)
                )

            # ── Seed translate_system if missing (en only) ──
            existing = await session.execute(
                select(SystemPrompt).where(
                    SystemPrompt.lang == "en",
                    SystemPrompt.key == "translate_system",
                )
            )
            if existing.scalar_one_or_none() is None:
                val = _EN_FALLBACK.get("translate_system", "")
                if val:
                    session.add(SystemPrompt(
                        lang="en", key="translate_system",
                        value=val, is_custom=False,
                    ))

            await session.commit()
            if legacy_rows:
                logger.info(
                    "PromptStore: collapsed %d legacy rows (%s) into unified "
                    "'system' key", len(legacy_rows), ", ".join(LEGACY_SYSTEM_KEYS),
                )

    async def _db_get(self, lang: str, key: str) -> str | None:
        if not self._session_factory:
            return None
        async with self._session_factory() as session:
            from core.registry.models import SystemPrompt
            result = await session.execute(
                select(SystemPrompt.value).where(
                    SystemPrompt.lang == lang, SystemPrompt.key == key
                )
            )
            row = result.scalar_one_or_none()
            return row if row else None

    async def _sync_defaults_from_json(self, lang: str) -> None:
        """Refresh non-custom rows for ``lang`` from the on-disk JSON file.

        Keeps DB defaults in sync with ``config/prompts/<lang>.json`` edits
        without touching rows the user has customised. Runs on every boot
        from :meth:`initialize`.
        """
        if not self._session_factory:
            return
        defaults = self._load_json_locale(lang)
        if not defaults:
            return
        async with self._session_factory() as session:
            from core.registry.models import SystemPrompt
            for key, val in defaults.items():
                if key not in PROMPT_KEYS or not val:
                    continue
                row = (await session.execute(
                    select(SystemPrompt).where(
                        SystemPrompt.lang == lang, SystemPrompt.key == key,
                    )
                )).scalar_one_or_none()
                if row is None:
                    session.add(SystemPrompt(
                        lang=lang, key=key, value=val, is_custom=False,
                    ))
                elif not row.is_custom and row.value != val:
                    row.value = val
            await session.commit()

    async def _seed_from_json(self, session: AsyncSession) -> None:
        """Seed DB with English defaults from en.json on first run.

        Only en.json is used as seed. Other languages are generated
        via LLM when the user selects a TTS voice in that language.
        """
        from core.registry.models import SystemPrompt
        en_path = _PROMPTS_DIR / "en.json"
        try:
            data = json.loads(en_path.read_text(encoding="utf-8")) if en_path.is_file() else {}
        except Exception:
            data = {}
        # Merge with hardcoded fallback
        for key in PROMPT_KEYS:
            val = data.get(key, _EN_FALLBACK.get(key, ""))
            if val:
                session.add(SystemPrompt(lang="en", key=key, value=val, is_custom=False))
        await session.commit()
        logger.info("PromptStore: seeded English defaults from en.json")

    @staticmethod
    def _load_json_locale(lang: str) -> dict[str, str]:
        """Load defaults from JSON file (for reset)."""
        path = _PROMPTS_DIR / f"{lang}.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        if lang != "en":
            path = _PROMPTS_DIR / "en.json"
            if path.is_file():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return dict(_EN_FALLBACK)


# ── Singleton ─────────────────────────────────────────────────────────────

_store: PromptStore | None = None


def get_prompt_store() -> PromptStore:
    global _store
    if _store is None:
        _store = PromptStore()
    return _store
