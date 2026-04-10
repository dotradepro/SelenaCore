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
    "hidden_system",         # System identity prompt — single prompt for all providers (template: {name}, {lang})
    "user_instructions",     # User instructions (appended to hidden_system)
    "intent_system",         # Intent classification prompt (template: {name}, {lang})
    "rephrase_system",       # TTS rephrase/generation prompt (template: {lang_name})
    "translate_system",      # System prompt for translation tasks
)
_PROMPTS_DIR = Path(os.environ.get("SELENA_PROMPTS_DIR", "/opt/selena-core/config/prompts"))

# English hardcoded fallback (if JSON files are also missing)
_EN_FALLBACK = {
    "user_instructions": "Keep answers short and helpful. You are a smart home assistant.",
    "hidden_system": (
        "You are {name}, smart home assistant. Reply ONLY in {lang}. "
        "Never say you are AI or mention model names/developers. "
        "If asked who you are — say: I am {name}, your home assistant. "
        "Created by SelenaCore team. "
        "TTS output — plain text only, no markdown/URLs/emoji."
    ),
    "intent_system": (
        "You are {name}, smart home assistant. Classify → JSON only.\n"
        '{{"intent":"namespace.action","params":{{}},"location":"<room or null>","response":"<short English>"}}\n'
        "RULES:\n"
        "1. Intent MUST have a dot: device.on, media.play, weather.query. NEVER bare words.\n"
        '2. Use "unknown" if request not in intents list.\n\n'
        "Examples:\n"
        '"turn on the light" → {{"intent":"device.on","location":null,"response":"Turning on."}}\n'
        '"turn off AC in living room" → {{"intent":"device.off","location":"living room","response":"Turning off."}}\n'
        '"what is the temperature" → {{"intent":"device.query_temperature","location":null,"response":"Checking."}}\n'
        '"lock the door" → {{"intent":"device.lock","location":null,"response":"Locking."}}\n'
        '"tell me a joke" → {{"intent":"unknown","location":null,"response":"I can\'t do that."}}'
    ),
    "rephrase_system": (
        "You are a smart home voice assistant. Speak ONLY {lang_name}.\n"
        "Rephrase naturally and concisely (1-2 sentences, no emoji, no markdown).\n"
        "Vary your phrasing — don't repeat the same structure.\n"
        "All numbers MUST be spelled out as words in {lang_name}.\n"
        "Translate ALL foreign words/names to {lang_name} or transliterate them.\n"
        "Output will be read aloud by TTS — no digits, no Latin letters, no symbols.\n"
        "Keep it short for TTS. Plain text only."
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
        """Seed DB from JSON files if empty, run migrations."""
        if not self._session_factory:
            return
        async with self._session_factory() as session:
            from core.registry.models import SystemPrompt
            result = await session.execute(select(SystemPrompt).limit(1))
            if result.scalar_one_or_none() is None:
                await self._seed_from_json(session)
        # Migrate: user_prompt+compact_user → user_instructions
        await self._migrate_user_instructions()

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
        """Migrate user_prompt → user_instructions, delete compact_user."""
        if not self._session_factory:
            return
        async with self._session_factory() as session:
            from core.registry.models import SystemPrompt
            # Check if old key exists
            old = await session.execute(
                select(SystemPrompt).where(SystemPrompt.key == "user_prompt")
            )
            old_rows = list(old.scalars())

            for row in old_rows:
                # Copy to user_instructions if not already present
                existing = await session.execute(
                    select(SystemPrompt).where(
                        SystemPrompt.lang == row.lang,
                        SystemPrompt.key == "user_instructions",
                    )
                )
                if existing.scalar_one_or_none() is None:
                    session.add(SystemPrompt(
                        lang=row.lang, key="user_instructions",
                        value=row.value, is_custom=row.is_custom,
                    ))
                await session.delete(row)

            # Delete compact_user rows
            compact = await session.execute(
                select(SystemPrompt).where(SystemPrompt.key == "compact_user")
            )
            for row in compact.scalars():
                await session.delete(row)

            # Merge rephrase_prompt into rephrase_system and delete rephrase_prompt
            rp_rows = await session.execute(
                select(SystemPrompt).where(SystemPrompt.key == "rephrase_prompt")
            )
            for row in rp_rows.scalars():
                # If rephrase_system still has {rephrase_rules}, replace it
                rs = await session.execute(
                    select(SystemPrompt).where(
                        SystemPrompt.lang == row.lang,
                        SystemPrompt.key == "rephrase_system",
                    )
                )
                rs_row = rs.scalar_one_or_none()
                if rs_row and "{rephrase_rules}" in rs_row.value:
                    rs_row.value = rs_row.value.replace("{rephrase_rules}", row.value)
                await session.delete(row)

            # Seed translate_system if missing (en only)
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

            # Drop the deprecated pattern_system key — pattern generation is now
            # an internal English-only operation hardcoded inside PatternGenerator,
            # not a user-editable DB prompt.
            await session.execute(
                delete(SystemPrompt).where(SystemPrompt.key == "pattern_system")
            )

            await session.commit()
            logger.info("PromptStore: migrated user_prompt→user_instructions, dropped pattern_system")

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
