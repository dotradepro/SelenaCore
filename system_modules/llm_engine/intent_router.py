"""
system_modules/llm_engine/intent_router.py — Three-tier Intent Router

Tier 0: FastMatcher + IntentCompiler (regex from YAML vocabulary) — zero latency
Tier 1: Local LLM (single call: intent JSON + response) — 300-800ms
Tier 2: Cloud LLM (OpenAI-compatible API, optional) — 1-3s

Orchestration:
  1. Try Fast Matcher first (keyword/regex YAML rules)
  2. Try system module registered intents (in-process regex)
  3. Try Module Bus (WebSocket user module intents)
  4. Check IntentCache (SQLite cache of previous LLM results)
  5. Try local LLM (single call with fixed system prompt)
  6. Try cloud LLM fallback (if configured)
  7. Fallback → "not understood"
  8. Publish voice.intent event to EventBus
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ── Fixed LLM system prompt (KV cache stays hot between requests) ──────

INTENT_SYSTEM_PROMPT = """\
You are Selena, a smart home assistant.
Analyze the user request. Reply ONLY with valid JSON, no extra text:
{
  "intent": "<intent_name or unknown>",
  "entity": "<device/object or null>",
  "location": "<room or null>",
  "params": {},
  "response": "<1-2 sentences in the user's language confirming the action>"
}

Known intents:
  device.on, device.off, device.set, device.query,
  media.play, media.stop, media.pause, media.resume, media.volume_set, media.next, media.prev,
  climate.set, climate.query,
  weather.query,
  automation.run, automation.list,
  presence.query,
  unknown

Rules:
- intent MUST be from the known list above or from the registered intents list
- entity, location — always in English (e.g. "light", "kitchen"), even if user speaks another language
- params — extracted parameters (e.g. {"level": 50} for volume, {"genre": "jazz"} for music)
- response — MUST be in the TTS language (specified at the end of this prompt), natural and concise
- If you cannot determine the intent, use "unknown" and provide a helpful response
"""


# ── Template responses for regex hits (no LLM needed) ──────────────────

_TEMPLATE_RESPONSES: dict[str, dict[str, str]] = {
    "device.on": {
        "en": "Turned on",
        "uk": "Увімкнено",
        "de": "Eingeschaltet",
        "fr": "Allumé",
        "es": "Encendido",
    },
    "device.off": {
        "en": "Turned off",
        "uk": "Вимкнено",
        "de": "Ausgeschaltet",
        "fr": "Éteint",
        "es": "Apagado",
    },
    "media.play": {
        "en": "Playing",
        "uk": "Вмикаю",
        "de": "Wird abgespielt",
        "fr": "Lecture en cours",
        "es": "Reproduciendo",
    },
    "media.stop": {
        "en": "Stopped",
        "uk": "Зупинено",
        "de": "Gestoppt",
        "fr": "Arrêté",
        "es": "Detenido",
    },
    "media.pause": {
        "en": "Paused",
        "uk": "Пауза",
        "de": "Pausiert",
        "fr": "En pause",
        "es": "En pausa",
    },
    "media.resume": {
        "en": "Resuming",
        "uk": "Продовжую",
        "de": "Fortgesetzt",
        "fr": "Reprise",
        "es": "Reanudado",
    },
    "privacy_on": {
        "en": "Privacy mode on",
        "uk": "Режим приватності увімкнено",
    },
    "privacy_off": {
        "en": "Privacy mode off",
        "uk": "Режим приватності вимкнено",
    },
}

_LANG_NAMES: dict[str, str] = {
    "uk": "Ukrainian", "en": "English", "de": "German",
    "fr": "French", "es": "Spanish", "pl": "Polish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch",
    "cs": "Czech", "ja": "Japanese", "zh": "Chinese",
    "ko": "Korean", "ru": "Russian", "tr": "Turkish",
}


# ── Param normalization: non-English captured values → English ─────────
_PARAM_NORMALIZE: dict[str, dict[str, str]] = {
    "genre": {
        "рок": "rock", "джаз": "jazz", "класику": "classical",
        "класичну": "classical", "ембієнт": "ambient", "поп": "pop",
        "новини": "news",
    },
}


def _get_template_response(intent: str, lang: str) -> str:
    """Get a template response for a regex-matched intent, or empty string."""
    templates = _TEMPLATE_RESPONSES.get(intent, {})
    return templates.get(lang, templates.get("en", ""))


@dataclass
class IntentResult:
    intent: str
    response: str
    action: dict[str, Any] | None
    source: str          # "fast_matcher" | "system_module" | "module_bus" | "llm" | "cloud" | "cache" | "fallback"
    latency_ms: int
    lang: str = "en"
    user_id: str | None = None
    params: dict[str, Any] | None = None
    raw_llm: str | None = None  # raw LLM response before parsing (debug)


# Re-export for backward compatibility
from system_modules.llm_engine.intent_compiler import SystemIntentEntry  # noqa: F401


class IntentRouter:
    """Intent router: DB regex → Module Bus → Cache → LLM → Cloud."""

    def __init__(self) -> None:
        self._system_prompt: str | None = None
        self._prompt_cache: dict[str, str] = {}  # lang → cached prompt

    # ── Legacy registration (no-op, kept for backward compat) ────────

    def register_system_intent(self, entry: SystemIntentEntry) -> None:
        """No-op. Intents are now loaded from DB by IntentCompiler."""
        pass

    def unregister_system_intents(self, module: str) -> None:
        """No-op. Intents are now loaded from DB by IntentCompiler."""
        pass

    # ── Main routing ────────────────────────────────────────────────────

    async def route(
        self,
        text: str,
        user_id: str | None = None,
        lang: str = "en",
        *,
        tts_lang: str | None = None,
        trace: bool = False,
    ) -> IntentResult | tuple[IntentResult, list[dict[str, Any]]]:
        """Route user text: DB regex → Module Bus → Cache → LLM → Cloud.

        Args:
            lang: STT-detected language (used for regex matching, cache key)
            tts_lang: TTS output language (used for response generation).
                      If None, defaults to lang.

        Returns IntentResult (or (IntentResult, trace_steps) when trace=True).
        """
        if tts_lang is None:
            tts_lang = lang
        start_ms = int(time.time() * 1000)
        steps: list[dict[str, Any]] = [] if trace else []

        def _elapsed() -> int:
            return int(time.time() * 1000) - start_ms

        # ── Tier 0: IntentCompiler (DB-driven regex, all patterns) ──
        from system_modules.llm_engine.intent_compiler import get_intent_compiler
        db_match = get_intent_compiler().match(text, lang=lang)

        if trace:
            steps.append({
                "tier": "0", "name": "IntentCompiler (DB)",
                "status": "hit" if db_match else "miss",
                "ms": _elapsed(),
                "detail": db_match["intent"] if db_match else None,
            })

        if db_match:
            response = _get_template_response(db_match["intent"], tts_lang)
            result = IntentResult(
                intent=db_match["intent"],
                response=response,
                action=None,
                source="system_module",
                latency_ms=_elapsed(),
                lang=lang,
                user_id=user_id,
                params=db_match.get("params", {}),
            )
            await self._publish_event(result, raw_text=text, lang=lang)
            return (result, steps) if trace else result

        # ── Tier 1: Module Bus (WebSocket user module intents) ──
        bus_hit = False
        bus_error = None
        try:
            from core.module_bus import get_module_bus
            bus_result = await get_module_bus().route_intent(
                text, lang, context={"user_id": user_id},
            )
            if bus_result is not None:
                if bus_result.get("handled"):
                    bus_hit = True
                    result = IntentResult(
                        intent=f"module.{bus_result.get('module', '?')}",
                        response=bus_result.get("tts_text", ""),
                        action=bus_result.get("data"),
                        source="module_bus",
                        latency_ms=_elapsed(),
                        lang=lang,
                        user_id=user_id,
                    )
                    if trace:
                        steps.append({
                            "tier": "0", "name": "Module Bus",
                            "status": "hit",
                            "ms": _elapsed(),
                            "detail": bus_result.get("module", "?"),
                        })
                    await self._publish_event(result, raw_text=text, lang=lang)
                    return (result, steps) if trace else result
                # Module unavailable
                reason = bus_result.get("reason", "")
                module_name = bus_result.get("module", "?")
                if reason in ("circuit_open", "timeout", "disconnected"):
                    logger.warning(
                        "Module bus: %s unavailable (reason=%s)", module_name, reason,
                    )
                    bus_error = f"{module_name}: {reason}"
                    from core.i18n import t
                    result = IntentResult(
                        intent=f"module.{module_name}",
                        response=t("intent.module_unavailable", lang=lang),
                        action=None,
                        source="module_bus",
                        latency_ms=_elapsed(),
                        lang=lang,
                        user_id=user_id,
                    )
                    if trace:
                        steps.append({
                            "tier": "0", "name": "Module Bus",
                            "status": "error",
                            "ms": _elapsed(),
                            "detail": bus_error,
                        })
                    await self._publish_event(result, raw_text=text, lang=lang)
                    return (result, steps) if trace else result
        except Exception as exc:
            logger.warning("Module bus error: %s", exc)
            bus_error = str(exc)

        if trace and not bus_hit:
            steps.append({
                "tier": "0", "name": "Module Bus",
                "status": "error" if bus_error else "miss",
                "ms": _elapsed(),
                "detail": bus_error,
            })

        # ── Check IntentCache (SQLite) before LLM ──
        cached = None
        try:
            from system_modules.llm_engine.intent_cache import get_intent_cache
            cached = await get_intent_cache().get(text, lang)
        except Exception:
            pass

        if cached:
            if trace:
                steps.append({
                    "tier": "cache", "name": "IntentCache",
                    "status": "hit",
                    "ms": _elapsed(),
                    "detail": cached.get("intent"),
                })
            result = IntentResult(
                intent=cached["intent"],
                response=cached.get("response", ""),
                action=None,
                source="cache",
                latency_ms=_elapsed(),
                lang=lang,
                user_id=user_id,
                params=cached.get("params", {}),
            )
            await self._publish_event(result, raw_text=text, lang=lang)
            return (result, steps) if trace else result
        elif trace:
            steps.append({
                "tier": "cache", "name": "IntentCache",
                "status": "miss",
                "ms": _elapsed(),
            })

        # ── Tier 1: Local LLM (single call) ──
        llm_result = None
        llm_error = None
        try:
            llm_result = await self._local_llm_classify(text, lang, tts_lang=tts_lang)
        except asyncio.TimeoutError:
            llm_error = "timeout"
            logger.warning("Local LLM timeout for: %s", text[:50])
        except Exception as exc:
            llm_error = str(exc)
            logger.warning("Local LLM error: %s", exc)

        if trace:
            steps.append({
                "tier": "1", "name": "Local LLM",
                "status": "hit" if llm_result else ("error" if llm_error else "skip"),
                "ms": _elapsed(),
                "detail": llm_result.intent if llm_result else llm_error,
            })

        if llm_result is not None:
            llm_result.latency_ms = _elapsed()
            llm_result.lang = lang
            llm_result.user_id = user_id
            # Cache successful classification
            if llm_result.intent not in ("unknown", "llm.response"):
                try:
                    from system_modules.llm_engine.intent_cache import get_intent_cache
                    await get_intent_cache().put(
                        text, lang, llm_result.intent,
                        llm_result.params or {},
                        llm_result.response,
                    )
                except Exception:
                    pass
            # Device disambiguation: resolve entity+location → device_id
            llm_result = await self._disambiguate_device(llm_result, tts_lang)
            await self._publish_event(llm_result, raw_text=text, lang=lang)
            return (llm_result, steps) if trace else llm_result

        # ── Tier 2: Cloud LLM (if configured) ──
        cloud_result = None
        cloud_error = None
        cloud_cfg = self._get_cloud_config()
        if cloud_cfg:
            try:
                cloud_result = await self._cloud_llm_classify(text, lang, cloud_cfg, tts_lang=tts_lang)
            except asyncio.TimeoutError:
                cloud_error = "timeout"
            except Exception as exc:
                cloud_error = str(exc)
                logger.warning("Cloud LLM error: %s", exc)

            if trace:
                steps.append({
                    "tier": "2", "name": "Cloud LLM",
                    "status": "hit" if cloud_result else ("error" if cloud_error else "skip"),
                    "ms": _elapsed(),
                    "detail": cloud_result.intent if cloud_result else cloud_error,
                })

            if cloud_result is not None:
                cloud_result.latency_ms = _elapsed()
                cloud_result.lang = lang
                cloud_result.user_id = user_id
                if cloud_result.intent not in ("unknown", "llm.response"):
                    try:
                        from system_modules.llm_engine.intent_cache import get_intent_cache
                        await get_intent_cache().put(
                            text, lang, cloud_result.intent,
                            cloud_result.params or {},
                            cloud_result.response,
                        )
                    except Exception:
                        pass
                # Device disambiguation
                cloud_result = await self._disambiguate_device(cloud_result, tts_lang)
                await self._publish_event(cloud_result, raw_text=text, lang=lang)
                return (cloud_result, steps) if trace else cloud_result
        elif trace:
            steps.append({
                "tier": "2", "name": "Cloud LLM",
                "status": "skip",
                "ms": _elapsed(),
                "detail": "not configured",
            })

        # ── Fallback ──
        from core.i18n import t
        fallback_msg = t("intent.fallback", lang=lang)

        if trace:
            steps.append({
                "tier": "—", "name": "Fallback",
                "status": "used",
                "ms": _elapsed(),
            })

        result = IntentResult(
            intent="unknown",
            response=fallback_msg,
            action=None,
            source="fallback",
            latency_ms=_elapsed(),
            lang=lang,
            user_id=user_id,
        )
        await self._publish_event(result, raw_text=text, lang=lang)
        return (result, steps) if trace else result

    # ── Local LLM (single call) ────────────────────────────────────────

    async def _local_llm_classify(self, text: str, lang: str, *, tts_lang: str | None = None) -> IntentResult | None:
        """Single LLM call: returns intent JSON + response in TTS language."""
        tts_lang = tts_lang or lang
        try:
            from core.config_writer import read_config
        except ImportError:
            return None

        config = read_config()

        # Read AI config (new format) or fall back to legacy voice/llm config
        ai_cfg = config.get("ai", {}).get("conversation", {})
        voice_cfg = config.get("voice", {})

        # Determine provider and model
        local_cfg = ai_cfg.get("local", {})
        host = local_cfg.get("host") or voice_cfg.get("ollama_url", "http://localhost:11434")
        model = local_cfg.get("model") or voice_cfg.get("llm_model", os.environ.get("OLLAMA_MODEL", "phi3:mini"))
        # Legacy provider config override
        p_model = voice_cfg.get("providers", {}).get("ollama", {}).get("model", "")
        if p_model:
            model = p_model

        options = local_cfg.get("options", {})
        temperature = options.get("temperature", 0.1)
        num_predict = options.get("num_predict", 80)

        # Build system prompt with registered intents catalog
        system_prompt = self._build_system_prompt(tts_lang)

        # Build user prompt with language tag for local models
        lang_name = _LANG_NAMES.get(lang, "English")
        tts_lang_name = _LANG_NAMES.get(tts_lang, "English")
        user_prompt = f"[spoken: {lang_name}, respond in: {tts_lang_name}] {text}"

        # Call Ollama
        from system_modules.llm_engine.ollama_client import get_ollama_client
        client = get_ollama_client()
        if not await client.is_available():
            return None

        raw = await asyncio.wait_for(
            client.generate(
                prompt=user_prompt, system=system_prompt,
                model=model, temperature=temperature,
            ),
            timeout=25.0,
        )

        if not raw:
            return None

        return self._parse_llm_response(raw, source="llm")

    # ── Cloud LLM ──────────────────────────────────────────────────────

    def _get_cloud_config(self) -> dict | None:
        """Get cloud LLM config if configured and enabled."""
        try:
            from core.config_writer import read_config
            config = read_config()
            ai_cfg = config.get("ai", {}).get("conversation", {})

            # Check new ai.conversation.cloud config
            cloud_cfg = ai_cfg.get("cloud", {})
            url = cloud_cfg.get("url", "")
            key = cloud_cfg.get("key") or os.environ.get("GROQ_API_KEY", "")
            model = cloud_cfg.get("model", "")

            if url and key and model:
                return {"url": url, "key": key, "model": model}

            # Fallback: check legacy voice.providers for cloud
            voice_cfg = config.get("voice", {})
            provider = voice_cfg.get("llm_provider", "")
            if provider not in ("ollama", "llamacpp", ""):
                providers_cfg = voice_cfg.get("providers", {})
                p_cfg = providers_cfg.get(provider, {})
                api_key = p_cfg.get("api_key", "")
                p_model = p_cfg.get("model", "")
                if api_key and p_model:
                    return {
                        "provider": provider,
                        "key": api_key,
                        "model": p_model,
                    }
        except Exception:
            pass
        return None

    async def _cloud_llm_classify(
        self, text: str, lang: str, cloud_cfg: dict, *, tts_lang: str | None = None,
    ) -> IntentResult | None:
        """Cloud LLM classification via OpenAI-compatible API or legacy providers."""
        tts_lang = tts_lang or lang
        lang_name = _LANG_NAMES.get(lang, "English")
        tts_lang_name = _LANG_NAMES.get(tts_lang, "English")
        system_prompt = self._build_system_prompt(tts_lang)
        user_prompt = f"[spoken: {lang_name}, respond in: {tts_lang_name}] {text}"

        # Legacy cloud providers
        legacy_provider = cloud_cfg.get("provider")
        if legacy_provider:
            from system_modules.llm_engine.cloud_providers import generate
            raw = await asyncio.wait_for(
                generate(
                    legacy_provider, cloud_cfg["key"], cloud_cfg["model"],
                    user_prompt, system_prompt, temperature=0.1,
                ),
                timeout=15.0,
            )
            if raw:
                return self._parse_llm_response(raw, source="cloud")
            return None

        # OpenAI-compatible API
        import httpx
        url = cloud_cfg["url"].rstrip("/")
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{url}/chat/completions",
                headers={"Authorization": f"Bearer {cloud_cfg['key']}"},
                json={
                    "model": cloud_cfg["model"],
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 200,
                },
            )
            resp.raise_for_status()
            raw = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")

        if raw:
            return self._parse_llm_response(raw, source="cloud")
        return None

    # ── Prompt building ────────────────────────────────────────────────

    def _build_system_prompt(self, lang: str) -> str:
        """Build system prompt with dynamic catalog from DB + in-memory intents.

        Cached per lang. Invalidated by refresh_system_prompt().
        """
        if lang in self._prompt_cache:
            return self._prompt_cache[lang]

        lang_name = _LANG_NAMES.get(lang, "English")

        # Start with fixed prompt
        prompt = INTENT_SYSTEM_PROMPT

        # Add known intents from IntentCompiler (DB-driven)
        try:
            from system_modules.llm_engine.intent_compiler import get_intent_compiler
            extra_intents: list[str] = []
            for ci in get_intent_compiler().get_all_intents():
                desc = ci.description or ci.intent
                params_keys = list(ci.params_schema.keys()) if ci.params_schema else []
                params_str = ", ".join(params_keys) if params_keys else "none"
                extra_intents.append(f"  {ci.intent}: {desc} (params: {params_str})")
            if extra_intents:
                prompt += "\nRegistered intents:\n" + "\n".join(extra_intents) + "\n"
        except Exception:
            pass

        # Dynamic catalog from DB (registered_modules, devices, radio_stations, scenes)
        db_catalog = self._load_db_catalog()
        if db_catalog:
            prompt += db_catalog

        # Language enforcement
        prompt += f"\nTTS language: {lang_name} ({lang}). Response MUST be in {lang_name}.\n"

        self._prompt_cache[lang] = prompt
        return prompt

    def _load_db_catalog(self) -> str:
        """Load dynamic catalog from DB (sync wrapper for startup speed).

        Returns prompt section string or empty string.
        """
        try:
            from core.module_loader.sandbox import get_sandbox
            sf = get_sandbox()._session_factory
            if sf is None:
                return ""

            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return ""

            # Use sync approach via thread to avoid nested async issues
            import threading
            result: list[str] = [""]

            def _sync_load() -> None:
                async def _inner() -> str:
                    from sqlalchemy import select
                    from core.registry.models import RegisteredModule, RadioStation, Scene, Device

                    parts: list[str] = []

                    async with sf() as session:
                        # Registered modules (enabled + connected)
                        stmt = select(RegisteredModule).where(
                            RegisteredModule.enabled == True,
                        )
                        modules = list((await session.execute(stmt)).scalars().all())
                        if modules:
                            lines = []
                            for m in modules:
                                intents = m.get_intents()
                                desc = m.description_en or m.name
                                if intents:
                                    lines.append(f"  {m.name}: {desc} (intents: {', '.join(intents)})")
                                else:
                                    lines.append(f"  {m.name}: {desc}")
                            parts.append("\nConnected modules:\n" + "\n".join(lines))

                        # Devices
                        devices = list((await session.execute(select(Device))).scalars().all())
                        if devices:
                            names = [d.name for d in devices[:30]]
                            parts.append(f"\nKnown devices: {', '.join(names)}")

                        # Radio stations
                        stmt = select(RadioStation).where(RadioStation.enabled == True)
                        stations = list((await session.execute(stmt)).scalars().all())
                        if stations:
                            items = [f"{s.name_en} ({s.genre_en})" if s.genre_en else s.name_en for s in stations[:20]]
                            parts.append(f"\nRadio stations: {', '.join(items)}")

                        # Scenes
                        stmt = select(Scene).where(Scene.enabled == True)
                        scenes = list((await session.execute(stmt)).scalars().all())
                        if scenes:
                            names = [s.name_en for s in scenes[:15]]
                            parts.append(f"\nScenes: {', '.join(names)}")

                    return "\n".join(parts)

                new_loop = asyncio.new_event_loop()
                try:
                    result[0] = new_loop.run_until_complete(_inner())
                finally:
                    new_loop.close()

            t = threading.Thread(target=_sync_load, daemon=True)
            t.start()
            t.join(timeout=3.0)
            return result[0]

        except Exception as exc:
            logger.debug("DB catalog load failed: %s", exc)
            return ""

    # ── Response parsing ───────────────────────────────────────────────

    def _parse_llm_response(self, raw: str, source: str = "llm") -> IntentResult | None:
        """Parse LLM JSON response into IntentResult."""
        raw_debug = raw

        # Strip code fences
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        # Find JSON object
        start_idx = cleaned.find("{")
        end_idx = cleaned.rfind("}")
        if start_idx == -1 or end_idx == -1:
            return IntentResult(
                intent="llm.response", response=cleaned, action=None,
                source=source, latency_ms=0, raw_llm=raw_debug,
            )

        try:
            data = json.loads(cleaned[start_idx:end_idx + 1])
        except json.JSONDecodeError:
            return IntentResult(
                intent="llm.response", response=cleaned, action=None,
                source=source, latency_ms=0, raw_llm=raw_debug,
            )

        intent_name = data.get("intent", "")
        params = data.get("params") or {}
        response = data.get("response", "")
        entity = data.get("entity")
        location = data.get("location")

        # Merge entity/location into params for module consumption
        if entity and entity != "null":
            params["entity"] = entity
        if location and location != "null":
            params["location"] = location

        if intent_name and intent_name != "unknown":
            return IntentResult(
                intent=intent_name, response=response, action=None,
                source=source, latency_ms=0, params=params, raw_llm=raw_debug,
            )
        elif response:
            return IntentResult(
                intent="llm.response", response=response, action=None,
                source=source, latency_ms=0, params=params, raw_llm=raw_debug,
            )

        return None

    # ── Legacy compatibility ───────────────────────────────────────────

    def _get_system_prompt(self) -> str:
        """Build system prompt — compact for local Ollama."""
        if self._system_prompt:
            return self._system_prompt
        from core.api.routes.voice_engines import build_system_prompt
        return build_system_prompt(compact=True)

    def set_system_prompt(self, prompt: str) -> None:
        self._system_prompt = prompt

    def refresh_system_prompt(self) -> None:
        self._system_prompt = None
        self._prompt_cache.clear()

    def _get_known_intent_names(self) -> set[str]:
        """Collect all known intent names from DB compiler + module bus."""
        names: set[str] = set()
        try:
            from system_modules.llm_engine.intent_compiler import get_intent_compiler
            for ci in get_intent_compiler().get_all_intents():
                names.add(ci.intent)
        except Exception:
            pass
        try:
            from core.module_bus import get_module_bus
            for item in get_module_bus()._intent_index:
                if hasattr(item, "module"):
                    names.add(f"module.{item.module}")
        except Exception:
            pass
        names.add("llm.response")
        return names

    # ── Device disambiguation ─────────────────────────────────────────

    async def _disambiguate_device(
        self, result: IntentResult, tts_lang: str,
    ) -> IntentResult:
        """If intent targets a device entity with >1 match, ask user to clarify.

        Uses entity_type + location from params to query DeviceRegistry.
        If exactly 1 device matches — injects device_id into params.
        If >1 match — replaces response with a clarification question.
        If 0 match — leaves result unchanged (module will handle).
        """
        params = result.params or {}
        entity = params.get("entity")
        location = params.get("location")

        # Only disambiguate device-related intents with entity info
        if not entity:
            return result

        try:
            from core.module_loader.sandbox import get_sandbox
            sandbox = get_sandbox()
            session_factory = sandbox._session_factory
            if session_factory is None:
                return result

            from core.registry.service import DeviceRegistry

            async with session_factory() as session:
                registry = DeviceRegistry(session)
                devices = await registry.query(
                    entity_type=entity,
                    location=location,
                )

            if len(devices) == 1:
                # Single match — inject device_id
                result.params = {**(result.params or {}), "device_id": devices[0].device_id}
            elif len(devices) > 1:
                # Multiple matches — ask for clarification
                from core.i18n import t
                device_names = ", ".join(d.name for d in devices[:5])
                result.intent = "disambiguation"
                result.response = t(
                    "intent.disambiguation",
                    lang=tts_lang,
                    devices=device_names,
                )
                result.action = None
                result.params = {
                    **(result.params or {}),
                    "candidates": [
                        {"device_id": d.device_id, "name": d.name, "location": d.location}
                        for d in devices[:5]
                    ],
                }
        except Exception as exc:
            logger.debug("Disambiguation failed: %s", exc)

        return result

    # ── Event publishing ───────────────────────────────────────────────

    @staticmethod
    def _normalize_params(params: dict[str, Any] | None) -> dict[str, Any]:
        """Normalize param values to English for internal EventBus communication.

        E.g. Ukrainian genre names captured by regex → English equivalents.
        """
        if not params:
            return {}
        result: dict[str, Any] = {}
        for key, val in params.items():
            if isinstance(val, str) and key in _PARAM_NORMALIZE:
                result[key] = _PARAM_NORMALIZE[key].get(val.lower(), val)
            else:
                result[key] = val
        return result

    async def _publish_event(self, result: IntentResult, raw_text: str = "", lang: str = "en") -> None:
        try:
            from core.eventbus.bus import get_event_bus
            from core.eventbus.types import VOICE_INTENT
            normalized_params = self._normalize_params(result.params)
            await get_event_bus().publish(
                type=VOICE_INTENT,
                source="core.intent_router",
                payload={
                    "intent": result.intent,
                    "response": result.response,
                    "action": result.action,
                    "params": normalized_params,
                    "source": result.source,
                    "user_id": result.user_id,
                    "latency_ms": result.latency_ms,
                    "raw_text": raw_text,
                    "lang": lang,
                },
            )
        except Exception as e:
            logger.debug("Intent event publish failed: %s", e)


_router: IntentRouter | None = None


def get_intent_router() -> IntentRouter:
    global _router
    if _router is None:
        _router = IntentRouter()
    return _router
