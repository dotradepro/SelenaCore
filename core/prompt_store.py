"""
core/prompt_store.py — Prompt storage service (SQLite + in-memory cache).

Stores LLM system prompts per language in the database.
JSON files in config/prompts/ serve as seed data for initial population.
Custom prompts (user-edited or LLM-translated) are flagged is_custom=True.

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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

PROMPT_KEYS = (
    "user_prompt",          # User instructions for cloud LLM (appended to hidden_system)
    "compact_user",         # User instructions for local LLM (appended to hidden_compact)
    "rephrase_prompt",       # Rephrase/TTS preparation instructions
    "hidden_system",         # System identity prompt for cloud LLM (template: {name}, {lang})
    "hidden_compact",        # System identity prompt for local LLM (template: {name}, {lang})
    "intent_system",         # Intent router LLM prompt (JSON format instructions)
    "rephrase_system",       # Rephrase wrapper prompt (template: {lang_name}, {rephrase_rules})
)
_PROMPTS_DIR = Path(os.environ.get("SELENA_PROMPTS_DIR", "/opt/selena-core/config/prompts"))

# English hardcoded fallback (if JSON files are also missing)
_EN_FALLBACK = {
    "user_prompt": "Keep answers short and helpful. You are a smart home assistant.",
    "compact_user": "Short answers, plain text.",
    "rephrase_prompt": (
        "The system performed an action and generated a default response.\n"
        "Rephrase it naturally and concisely (1 sentence, no emoji, no markdown).\n"
        "Vary your phrasing — don't repeat the same structure.\n"
        "Keep it short for TTS. Plain text only."
    ),
    "hidden_system": (
        "You are {name}. "
        "CRITICAL: Reply ONLY in {lang}. Every word MUST be in {lang}. "
        "Do NOT insert words from other languages in any combination. "
        "NEVER say you are AI, a language model, or neural network. "
        "NEVER mention model names, versions, or developers (Google, OpenAI, Meta, Anthropic, etc.). "
        "If asked who you are — say: I am {name}, your home assistant. "
        "If asked who created you — say: the SelenaCore team. "
        "Response will be read by TTS — plain text only, no markdown/URLs/emoji."
    ),
    "hidden_compact": (
        "You are {name}. {lang} only, no other languages. "
        "Never say you are AI or mention model names."
    ),
    "intent_system": (
        "You are {name}, a smart home assistant.\n"
        "Analyze the user request. Reply ONLY with valid JSON, no extra text:\n"
        '{{\n  "intent": "<intent_name or unknown>",\n'
        '  "entity": "<device/object or null>",\n'
        '  "location": "<room or null>",\n'
        '  "params": {{}},\n'
        '  "pattern": "<short English command phrase (2-5 words)>",\n'
        '  "response": "<1-2 sentences in {lang} confirming the action>"\n}}\n\n'
        "Rules:\n"
        "- intent MUST be from the known list or registered intents\n"
        "- entity, location — always in English\n"
        "- params — extracted parameters\n"
        "- pattern — MUST be in English, short voice command\n"
        "- response — MUST be in {lang}, natural and concise\n"
        "- If unknown intent, use 'unknown' and provide helpful response"
    ),
    "rephrase_system": (
        "You are a smart home voice assistant. Speak ONLY {lang_name}.\n"
        "{rephrase_rules}\n"
        "CRITICAL: All numbers MUST be spelled out as words in {lang_name}.\n"
        "CRITICAL: Translate ALL foreign words/names to {lang_name} or transliterate them.\n"
        "Output will be read aloud by TTS — no digits, no Latin letters, no symbols."
    ),
}

from core.lang_utils import lang_code_to_name


class PromptStore:
    """Prompt storage with DB persistence and in-memory cache."""

    def __init__(self) -> None:
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._cache: dict[str, dict[str, str]] = {}  # {lang: {key: value}}

    def set_session_factory(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = factory

    async def initialize(self) -> None:
        """Seed DB from JSON files if empty, then load cache."""
        if not self._session_factory:
            return
        async with self._session_factory() as session:
            from core.registry.models import SystemPrompt
            result = await session.execute(select(SystemPrompt).limit(1))
            if result.scalar_one_or_none() is None:
                await self._seed_from_json(session)
            await self._load_cache(session)

    async def get(self, lang: str, key: str) -> str:
        """Get a prompt by language and key. Falls back to en, then hardcoded."""
        # Cache first
        if lang in self._cache and key in self._cache[lang]:
            return self._cache[lang][key]
        # Try DB
        val = await self._db_get(lang, key)
        if val is not None:
            return val
        # Fallback to English
        if lang != "en":
            return await self.get("en", key)
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
        """Save a prompt. Updates cache and DB."""
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
        # Update cache
        self._cache.setdefault(lang, {})[key] = value

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

        lang_name = lang_code_to_name(lang)
        en_prompts = await self.get_all("en")

        try:
            from system_modules.llm_engine.cloud_providers import generate as llm_generate

            api_key, provider, model = _find_llm_provider()
            if not api_key:
                logger.warning("No LLM provider for prompt generation")
                return False

            for key, en_text in en_prompts.items():
                if not en_text:
                    continue
                prompt = (
                    f"Translate this voice assistant system prompt to {lang_name}. "
                    f"Keep the same meaning, tone, structure, and formatting exactly. "
                    f"Output ONLY the translated text, nothing else.\n\n"
                    f"{en_text}"
                )
                translated = await llm_generate(
                    provider=provider, api_key=api_key, model=model,
                    prompt=prompt, temperature=0.2,
                )
                if translated and translated.strip():
                    await self.set(lang, key, translated.strip(), is_custom=False)
                    logger.info("Generated prompt '%s' for lang=%s", key, lang)

            return True
        except Exception as e:
            logger.warning("Prompt generation for %s failed: %s", lang, e)
            return False

    async def translate_custom_prompts(self, old_lang: str, new_lang: str) -> None:
        """Translate user-edited (custom) prompts from old_lang to new_lang via LLM."""
        new_lang_name = lang_code_to_name(new_lang)

        api_key, provider, model = _find_llm_provider()
        if not api_key:
            return

        try:
            from system_modules.llm_engine.cloud_providers import generate as llm_generate

            for key in PROMPT_KEYS:
                meta = await self.get_meta(old_lang, key)
                if not meta["is_custom"]:
                    continue
                prompt = (
                    f"Translate this voice assistant system prompt to {new_lang_name}. "
                    f"Keep the same meaning, tone, and formatting. "
                    f"Output ONLY the translated text, nothing else.\n\n"
                    f"{meta['value']}"
                )
                translated = await llm_generate(
                    provider=provider, api_key=api_key, model=model,
                    prompt=prompt, temperature=0.2,
                )
                if translated and translated.strip():
                    await self.set(new_lang, key, translated.strip(), is_custom=True)
                    logger.info("Translated custom prompt '%s': %s → %s", key, old_lang, new_lang)
        except Exception as e:
            logger.warning("Custom prompt translation failed: %s", e)

    # ── Private ───────────────────────────────────────────────────────────

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

    async def _load_cache(self, session: AsyncSession) -> None:
        """Load all prompts into memory cache."""
        from core.registry.models import SystemPrompt
        result = await session.execute(select(SystemPrompt))
        self._cache.clear()
        for row in result.scalars():
            self._cache.setdefault(row.lang, {})[row.key] = row.value
        logger.info("PromptStore: cached %d prompts for %d languages",
                     sum(len(v) for v in self._cache.values()), len(self._cache))

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


def _find_llm_provider() -> tuple[str, str, str]:
    """Find an available LLM provider. Returns (api_key, provider, model)."""
    _env_keys = {
        "google": ("GEMINI_API_KEY", "gemini-2.0-flash"),
        "openai": ("OPENAI_API_KEY", "gpt-4o-mini"),
        "anthropic": ("ANTHROPIC_API_KEY", "claude-sonnet-4-20250514"),
        "groq": ("GROQ_API_KEY", "llama-3.1-8b-instant"),
    }
    try:
        from core.config_writer import read_config
        llm_cfg = read_config().get("llm", {})
    except Exception:
        llm_cfg = {}

    for prov, (env_name, default_model) in _env_keys.items():
        key = llm_cfg.get(f"{prov}_api_key", "") or os.getenv(env_name, "")
        if key:
            model = llm_cfg.get(f"{prov}_model", default_model)
            return key, prov, model
    return "", "", ""


# ── Singleton ─────────────────────────────────────────────────────────────

_store: PromptStore | None = None


def get_prompt_store() -> PromptStore:
    global _store
    if _store is None:
        _store = PromptStore()
    return _store
