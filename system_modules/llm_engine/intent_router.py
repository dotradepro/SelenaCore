"""
system_modules/llm_engine/intent_router.py — Multi-tier Intent Router

Tier 1:   Fast Matcher (keyword/regex YAML rules) — zero latency
Tier 1.5: System Module Intents (in-process regex) — microseconds
Tier 2:   Module Bus (WebSocket intent routing) — milliseconds
Tier 3a:  Cloud LLM Intent Classification (Gemini/OpenAI/etc.) — seconds
Tier 3b:  Ollama LLM fallback — dynamic understanding (RAM >= 5GB)

Orchestration:
  1. Try Fast Matcher first
  2. Try system module registered intents (in-process, no HTTP)
  3. Try user module intents via Module Bus (WebSocket)
  4. Try cloud LLM intent classification (if cloud provider configured)
  5. Try local Ollama LLM (if RAM >= 5GB)
  6. Fallback → "not understood"
  7. Publish voice.intent event to EventBus
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class IntentResult:
    intent: str
    response: str
    action: dict[str, Any] | None
    source: str          # "fast_matcher" | "system_module" | "module_bus" | "cloud_llm" | "llm" | "fallback"
    latency_ms: int
    user_id: str | None = None
    params: dict[str, Any] | None = None


@dataclass
class SystemIntentEntry:
    """In-process intent registration for SYSTEM modules."""
    module: str                                    # e.g. "media-player"
    intent: str                                    # e.g. "media.play_radio"
    patterns: dict[str, list[str]]                 # {"uk": [...], "en": [...]}
    description: str = ""
    priority: int = 0                              # higher = checked first


class IntentRouter:
    """Orchestrates Fast Matcher + System Intents + Module Intents + LLM."""

    def __init__(self) -> None:
        self._system_prompt: str | None = None
        self._system_intents: list[SystemIntentEntry] = []

    # ── System module intent registration ──────────────────────────────

    def register_system_intent(self, entry: SystemIntentEntry) -> None:
        """Register an in-process intent from a SYSTEM module."""
        self._system_intents.append(entry)
        self._system_intents.sort(key=lambda e: e.priority, reverse=True)
        logger.info(
            "IntentRouter: registered system intent '%s' from '%s'",
            entry.intent, entry.module,
        )

    def unregister_system_intents(self, module: str) -> None:
        """Remove all intent registrations for a system module."""
        before = len(self._system_intents)
        self._system_intents = [e for e in self._system_intents if e.module != module]
        removed = before - len(self._system_intents)
        if removed:
            logger.info("IntentRouter: unregistered %d intents from '%s'", removed, module)

    def _match_system_intents(
        self, text: str, lang: str,
    ) -> tuple[SystemIntentEntry, dict[str, str]] | None:
        """Try matching text against system module intent patterns.

        Returns (entry, params) on match, None otherwise.
        Tries requested language first, falls back to 'en'.
        """
        text_lower = text.lower().strip()
        for entry in self._system_intents:
            lang_patterns = entry.patterns.get(lang) or entry.patterns.get("en", [])
            for pattern in lang_patterns:
                try:
                    m = re.search(pattern, text_lower, re.IGNORECASE)
                    if m:
                        logger.debug(
                            "System intent match: '%s' → '%s' (module=%s)",
                            pattern, entry.intent, entry.module,
                        )
                        return entry, m.groupdict() or {}
                except re.error:
                    logger.warning(
                        "Invalid regex '%s' in system intent '%s'",
                        pattern, entry.intent,
                    )
        return None

    # ── Main routing ────────────────────────────────────────────────────

    async def route(
        self,
        text: str,
        user_id: str | None = None,
        lang: str = "en",
        *,
        trace: bool = False,
    ) -> IntentResult | tuple[IntentResult, list[dict[str, Any]]]:
        """Route user text to the appropriate intent handler.

        Resolution order:
          Tier 1:   Fast Matcher (keyword/regex YAML rules) — zero latency
          Tier 1.5: System Module Intents (in-process regex) — microseconds
          Tier 2:   Module Bus (WebSocket intent routing) — milliseconds
          Tier 3:   LLM fallback — dynamic understanding, disabled when RAM < 5GB

        Returns IntentResult (or (IntentResult, trace_steps) when trace=True).
        """
        start_ms = int(time.time() * 1000)
        steps: list[dict[str, Any]] = [] if trace else []

        def _elapsed() -> int:
            return int(time.time() * 1000) - start_ms

        # Tier 1: Fast Matcher
        from system_modules.llm_engine.fast_matcher import get_fast_matcher
        match = get_fast_matcher().match(text, lang=lang)

        if trace:
            steps.append({
                "tier": "1", "name": "Fast Matcher",
                "status": "hit" if match else "miss",
                "ms": _elapsed(),
                "detail": match.intent if match else None,
            })

        if match:
            result = IntentResult(
                intent=match.intent,
                response=match.response or "",
                action=match.action,
                source="fast_matcher",
                latency_ms=_elapsed(),
                user_id=user_id,
                params=match.params or {},
            )
            await self._publish_event(result)
            return (result, steps) if trace else result

        # Tier 1.5: System Module Intents — in-process, no HTTP
        sys_match = self._match_system_intents(text, lang)

        if trace:
            steps.append({
                "tier": "1.5", "name": "System Module Intents",
                "status": "hit" if sys_match else "miss",
                "ms": _elapsed(),
                "detail": f"{sys_match[0].module}::{sys_match[0].intent}" if sys_match else None,
                "registered": len(self._system_intents),
            })

        if sys_match is not None:
            entry, params = sys_match
            result = IntentResult(
                intent=entry.intent,
                response="",
                action=None,
                source="system_module",
                latency_ms=_elapsed(),
                user_id=user_id,
                params=params,
            )
            await self._publish_event(result)
            return (result, steps) if trace else result

        # Tier 2: Module Bus — route intent via WebSocket bus
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
                        user_id=user_id,
                    )
                    if trace:
                        steps.append({
                            "tier": "2", "name": "Module Bus",
                            "status": "hit",
                            "ms": _elapsed(),
                            "detail": bus_result.get("module", "?"),
                        })
                    await self._publish_event(result)
                    return (result, steps) if trace else result
                # Bus matched but module unavailable (circuit_open/timeout/disconnected)
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
                        user_id=user_id,
                    )
                    if trace:
                        steps.append({
                            "tier": "2", "name": "Module Bus",
                            "status": "error",
                            "ms": _elapsed(),
                            "detail": bus_error,
                        })
                    await self._publish_event(result)
                    return (result, steps) if trace else result
        except Exception as exc:
            logger.warning("Module bus Tier 2 error: %s", exc)
            bus_error = str(exc)

        if trace and not bus_hit:
            steps.append({
                "tier": "2", "name": "Module Bus",
                "status": "error" if bus_error else "miss",
                "ms": _elapsed(),
                "detail": bus_error,
            })

        # Tier 3a: Cloud LLM Intent Classification
        cloud_result = None
        cloud_error = None
        cloud_skipped = False
        try:
            cloud_result = await self._try_cloud_classification(text, lang)
            if cloud_result is None:
                cloud_skipped = True
        except asyncio.TimeoutError:
            cloud_error = "timeout (15s)"
            logger.warning("Cloud LLM classification timeout for: %s", text[:50])
        except Exception as exc:
            cloud_error = str(exc)
            logger.warning("Cloud LLM classification error: %s", exc)

        if trace:
            if cloud_result:
                steps.append({
                    "tier": "3a", "name": "Cloud LLM",
                    "status": "hit",
                    "ms": _elapsed(),
                    "detail": cloud_result.intent,
                })
            else:
                steps.append({
                    "tier": "3a", "name": "Cloud LLM",
                    "status": "error" if cloud_error else ("skip" if cloud_skipped else "miss"),
                    "ms": _elapsed(),
                    "detail": cloud_error or ("no cloud provider" if cloud_skipped else None),
                })

        if cloud_result is not None:
            cloud_result.latency_ms = _elapsed()
            cloud_result.user_id = user_id
            await self._publish_event(cloud_result)
            return (cloud_result, steps) if trace else cloud_result

        # Tier 3b: Ollama LLM (local)
        from system_modules.llm_engine.ollama_client import get_ollama_client, _should_use_llm
        llm_available = _should_use_llm()
        llm_error = None

        if llm_available:
            try:
                system_prompt = self._get_system_prompt()
                client = get_ollama_client()
                llm_response = await asyncio.wait_for(
                    client.generate(prompt=text, system=system_prompt),
                    timeout=25.0,
                )
                if llm_response:
                    if trace:
                        steps.append({
                            "tier": "3b", "name": "Ollama LLM",
                            "status": "hit",
                            "ms": _elapsed(),
                            "detail": None,
                        })
                    result = IntentResult(
                        intent="llm.response",
                        response=llm_response,
                        action=None,
                        source="llm",
                        latency_ms=_elapsed(),
                        user_id=user_id,
                    )
                    await self._publish_event(result)
                    return (result, steps) if trace else result
            except asyncio.TimeoutError:
                logger.warning("LLM timeout for input: %s", text[:50])
                llm_error = "timeout (25s)"
            except Exception as e:
                logger.error("LLM error: %s", e)
                llm_error = str(e)

        if trace:
            steps.append({
                "tier": "3b", "name": "Ollama LLM",
                "status": "skip" if not llm_available else ("error" if llm_error else "miss"),
                "ms": _elapsed(),
                "detail": "RAM < 5GB" if not llm_available else llm_error,
            })

        # Fallback — language-aware "not understood" via i18n
        from core.i18n import t
        fallback_msg = t("intent.fallback", lang=lang)

        if trace:
            steps.append({
                "tier": "—", "name": "Fallback",
                "status": "used",
                "ms": _elapsed(),
                "detail": None,
            })

        result = IntentResult(
            intent="unknown",
            response=fallback_msg,
            action=None,
            source="fallback",
            latency_ms=_elapsed(),
            user_id=user_id,
        )
        await self._publish_event(result)
        return (result, steps) if trace else result

    # ── Cloud LLM Intent Classification ──────────────────────────────

    def _get_known_intent_names(self) -> set[str]:
        """Collect all known intent names from fast matcher + system + bus intents."""
        names: set[str] = set()
        try:
            from system_modules.llm_engine.fast_matcher import get_fast_matcher
            for rule in get_fast_matcher()._rules:
                name = rule.get("name", "")
                if name:
                    names.add(name)
        except Exception:
            pass
        for entry in self._system_intents:
            names.add(entry.intent)
        # Module Bus intents
        try:
            from core.module_bus import get_module_bus
            for item in get_module_bus()._intent_index:
                if hasattr(item, "module") and hasattr(item, "description"):
                    names.add(f"module.{item.module}")
        except Exception:
            pass
        names.add("llm.response")
        return names

    def _build_intent_catalog(self, lang: str) -> str:
        """Build a text catalog of all known intents for the classification prompt."""
        seen: set[str] = set()
        lines: list[str] = []

        # System intents first (richer metadata)
        for entry in self._system_intents:
            if entry.intent in seen:
                continue
            seen.add(entry.intent)
            # Extract param names from regex patterns
            param_names: set[str] = set()
            for patterns in entry.patterns.values():
                for p in patterns:
                    param_names.update(re.findall(r"\?P<(\w+)>", p))
            params_str = ", ".join(sorted(param_names)) if param_names else "none"
            desc = entry.description or entry.intent
            lines.append(f"- {entry.intent}: {desc}. Params: {params_str}")

        # Fast matcher rules
        try:
            from system_modules.llm_engine.fast_matcher import get_fast_matcher
            for rule in get_fast_matcher()._rules:
                name = rule.get("name", "")
                if not name or name in seen:
                    continue
                seen.add(name)
                keywords = rule.get("keywords", [])[:4]
                examples = ", ".join(f'"{k}"' for k in keywords) if keywords else ""
                lines.append(f"- {name}: Examples: {examples}. Params: none")
        except Exception:
            pass

        # Module Bus intents (user modules)
        try:
            from core.module_bus import get_module_bus
            for item in get_module_bus()._intent_index:
                module_name = getattr(item, "module", "")
                intent_key = f"module.{module_name}"
                if intent_key in seen:
                    continue
                seen.add(intent_key)
                desc = getattr(item, "description", module_name)
                lines.append(f"- {intent_key}: {desc}. Params: none")
        except Exception:
            pass

        return "\n".join(lines)

    def _build_classification_prompt(self, text: str, lang: str) -> tuple[str, str]:
        """Build (system_prompt, user_prompt) for cloud LLM intent classification."""
        lang_names = {"uk": "Ukrainian", "en": "English", "de": "German", "fr": "French", "es": "Spanish"}
        lang_name = lang_names.get(lang, "English")
        catalog = self._build_intent_catalog(lang)

        system_prompt = (
            "You are a voice command classifier for a smart home assistant.\n"
            "Classify the user's voice command into one of the known intents.\n\n"
            f"User language: {lang_name} ({lang}).\n\n"
            f"Known intents:\n{catalog}\n\n"
            "Rules:\n"
            '1. If the command matches a known intent, respond: {"intent": "<name>", "params": {<extracted params>}}\n'
            "2. Extract parameters when applicable (genre, station_name, query, level, etc.).\n"
            '3. If the command is a general question or conversation, respond: {"intent": "llm.response", "params": {}, '
            f'"response": "<helpful answer in {lang_name}>"}}\n'
            "4. Output ONLY valid JSON. No markdown, no code fences, no explanation.\n"
        )
        return system_prompt, text

    async def _try_cloud_classification(self, text: str, lang: str) -> IntentResult | None:
        """Attempt intent classification via configured cloud LLM provider."""
        try:
            from core.config_writer import read_config
        except ImportError:
            return None

        config = read_config()
        voice_cfg = config.get("voice", {})
        provider = voice_cfg.get("llm_provider", "ollama")

        # Skip if no cloud provider active
        if provider in ("ollama", "llamacpp"):
            return None

        providers_cfg = voice_cfg.get("providers", {})
        p_cfg = providers_cfg.get(provider, {})
        api_key = p_cfg.get("api_key", "")
        model = p_cfg.get("model", "")

        if not api_key or not model:
            return None

        system_prompt, user_prompt = self._build_classification_prompt(text, lang)

        from system_modules.llm_engine.cloud_providers import generate
        raw = await asyncio.wait_for(
            generate(provider, api_key, model, user_prompt, system_prompt, temperature=0.0),
            timeout=15.0,
        )

        if not raw:
            return None

        # Parse JSON — strip code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        # Find JSON object
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1:
            # Not JSON — use raw text as conversational response
            return IntentResult(
                intent="llm.response", response=cleaned, action=None,
                source="cloud_llm", latency_ms=0,
            )

        data = json.loads(cleaned[start:end + 1])
        intent_name = data.get("intent", "")
        params = data.get("params") or {}
        response = data.get("response", "")

        known = self._get_known_intent_names()
        if intent_name and intent_name in known:
            return IntentResult(
                intent=intent_name, response=response, action=None,
                source="cloud_llm", latency_ms=0, params=params,
            )
        elif intent_name == "llm.response" or response:
            return IntentResult(
                intent="llm.response", response=response, action=None,
                source="cloud_llm", latency_ms=0, params=params,
            )

        return None

    def _get_system_prompt(self) -> str:
        """Build system prompt — compact for local Ollama, via build_system_prompt()."""
        if self._system_prompt:
            return self._system_prompt

        # Intent router uses local Ollama → compact prompt for small models
        from core.api.routes.voice_engines import build_system_prompt
        return build_system_prompt(compact=True)

    def set_system_prompt(self, prompt: str) -> None:
        """Override the LLM system prompt (manual override, bypasses config)."""
        self._system_prompt = prompt

    def refresh_system_prompt(self) -> None:
        """Clear cached prompt so it re-reads from config on next call."""
        self._system_prompt = None

    async def _publish_event(self, result: IntentResult) -> None:
        try:
            from core.eventbus.bus import get_event_bus
            from core.eventbus.types import VOICE_INTENT
            await get_event_bus().publish(
                type=VOICE_INTENT,
                source="core.intent_router",
                payload={
                    "intent": result.intent,
                    "response": result.response,
                    "action": result.action,
                    "params": result.params or {},
                    "source": result.source,
                    "user_id": result.user_id,
                    "latency_ms": result.latency_ms,
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
