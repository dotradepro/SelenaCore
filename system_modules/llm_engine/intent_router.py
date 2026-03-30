"""
system_modules/llm_engine/intent_router.py — Multi-tier Intent Router

Tier 1:   Fast Matcher (keyword/regex YAML rules) — zero latency
Tier 1.5: System Module Intents (in-process regex) — microseconds
Tier 2:   Module Bus (WebSocket intent routing) — milliseconds
Tier 3:   Ollama LLM fallback — dynamic understanding

Orchestration:
  1. Try Fast Matcher first
  2. Try system module registered intents (in-process, no HTTP)
  3. Try user module intents via Module Bus (WebSocket)
  4. If no match → route to Ollama with dynamic system prompt
  5. If RAM < 5GB → Ollama disabled, return "not understood"
  6. Publish voice.intent event to EventBus
"""
from __future__ import annotations

import asyncio
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
    source: str          # "fast_matcher" | "system_module" | "module_bus" | "llm" | "fallback"
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
    ) -> IntentResult:
        """Route user text to the appropriate intent handler.

        Resolution order:
          Tier 1:   Fast Matcher (keyword/regex YAML rules) — zero latency
          Tier 1.5: System Module Intents (in-process regex) — microseconds
          Tier 2:   Module Bus (WebSocket intent routing) — milliseconds
          Tier 3:   LLM fallback — dynamic understanding, disabled when RAM < 5GB

        Returns IntentResult with the resolved intent, response, and action.
        """
        start_ms = int(time.time() * 1000)

        # Tier 1: Fast Matcher
        from system_modules.llm_engine.fast_matcher import get_fast_matcher
        match = get_fast_matcher().match(text, lang=lang)

        if match:
            result = IntentResult(
                intent=match.intent,
                response=match.response or "",
                action=match.action,
                source="fast_matcher",
                latency_ms=int(time.time() * 1000) - start_ms,
                user_id=user_id,
                params=match.params or {},
            )
            await self._publish_event(result)
            return result

        # Tier 1.5: System Module Intents — in-process, no HTTP
        sys_match = self._match_system_intents(text, lang)
        if sys_match is not None:
            entry, params = sys_match
            result = IntentResult(
                intent=entry.intent,
                response="",
                action=None,
                source="system_module",
                latency_ms=int(time.time() * 1000) - start_ms,
                user_id=user_id,
                params=params,
            )
            await self._publish_event(result)
            return result

        # Tier 2: Module Bus — route intent via WebSocket bus
        try:
            from core.module_bus import get_module_bus
            bus_result = await get_module_bus().route_intent(
                text, lang, context={"user_id": user_id},
            )
            if bus_result is not None:
                if bus_result.get("handled"):
                    result = IntentResult(
                        intent=f"module.{bus_result.get('module', '?')}",
                        response=bus_result.get("tts_text", ""),
                        action=bus_result.get("data"),
                        source="module_bus",
                        latency_ms=int(time.time() * 1000) - start_ms,
                        user_id=user_id,
                    )
                    await self._publish_event(result)
                    return result
                # Bus matched but module unavailable (circuit_open/timeout/disconnected)
                reason = bus_result.get("reason", "")
                module_name = bus_result.get("module", "?")
                if reason in ("circuit_open", "timeout", "disconnected"):
                    logger.warning(
                        "Module bus: %s unavailable (reason=%s)", module_name, reason,
                    )
                    from core.i18n import t
                    result = IntentResult(
                        intent=f"module.{module_name}",
                        response=t("intent.module_unavailable", lang=lang),
                        action=None,
                        source="module_bus",
                        latency_ms=int(time.time() * 1000) - start_ms,
                        user_id=user_id,
                    )
                    await self._publish_event(result)
                    return result
        except Exception as exc:
            logger.warning("Module bus Tier 2 error: %s", exc)

        # Tier 3: LLM
        from system_modules.llm_engine.ollama_client import get_ollama_client, _should_use_llm
        if _should_use_llm():
            try:
                system_prompt = self._get_system_prompt()
                client = get_ollama_client()
                llm_response = await asyncio.wait_for(
                    client.generate(prompt=text, system=system_prompt),
                    timeout=25.0,
                )
                if llm_response:
                    result = IntentResult(
                        intent="llm.response",
                        response=llm_response,
                        action=None,
                        source="llm",
                        latency_ms=int(time.time() * 1000) - start_ms,
                        user_id=user_id,
                    )
                    await self._publish_event(result)
                    return result
            except asyncio.TimeoutError:
                logger.warning("LLM timeout for input: %s", text[:50])
            except Exception as e:
                logger.error("LLM error: %s", e)

        # Fallback — language-aware "not understood" via i18n
        from core.i18n import t
        fallback_msg = t("intent.fallback", lang=lang)

        result = IntentResult(
            intent="unknown",
            response=fallback_msg,
            action=None,
            source="fallback",
            latency_ms=int(time.time() * 1000) - start_ms,
            user_id=user_id,
        )
        await self._publish_event(result)
        return result

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
