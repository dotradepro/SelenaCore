"""
system_modules/llm_engine/intent_router.py — Two-tier Intent Router

Tier 1: Fast Matcher (keyword/regex) — zero latency
Tier 2: Ollama LLM fallback — dynamic understanding

Orchestration:
  1. Try Fast Matcher first
  2. If no match → route to Ollama with dynamic system prompt
  3. If RAM < 5GB → Ollama disabled, return "not understood"
  4. Publish voice.intent event to EventBus
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class IntentResult:
    intent: str
    response: str
    action: dict[str, Any] | None
    source: str          # "fast_matcher" | "llm" | "fallback"
    latency_ms: int
    user_id: str | None = None


class IntentRouter:
    """Orchestrates Fast Matcher + LLM for intent resolution."""

    def __init__(self) -> None:
        self._system_prompt: str | None = None

    async def route(
        self,
        text: str,
        user_id: str | None = None,
        lang: str = "en",
    ) -> IntentResult:
        """Route user text to the appropriate intent handler.

        Resolution order:
          Tier 1: Fast Matcher (keyword/regex YAML rules) — zero latency
          Tier 2: Module Intents (registered via /api/v1/intents) — no LLM cost
          Tier 3: Ollama LLM — dynamic understanding, disabled when RAM < 5GB

        Returns IntentResult with the resolved intent, response, and action.
        """
        start_ms = int(time.time() * 1000)

        # Tier 1: Fast Matcher
        from system_modules.llm_engine.fast_matcher import get_fast_matcher
        match = get_fast_matcher().match(text)

        if match:
            result = IntentResult(
                intent=match.intent,
                response=match.response or "",
                action=match.action,
                source="fast_matcher",
                latency_ms=int(time.time() * 1000) - start_ms,
                user_id=user_id,
            )
            await self._publish_event(result)
            return result

        # Tier 2: Module Intents — ask registered modules before hitting LLM
        try:
            from core.api.routes.intents import find_module_for_text
            module_match = find_module_for_text(text, lang)
            if module_match is not None:
                module_name, port, endpoint = module_match
                import httpx
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.post(
                        f"http://localhost:{port}{endpoint}",
                        json={"text": text, "lang": lang, "context": {"user_id": user_id}},
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("handled"):
                        result = IntentResult(
                            intent=f"module.{module_name}",
                            response=data.get("tts_text", ""),
                            action=data.get("data"),
                            source="module_intent",
                            latency_ms=int(time.time() * 1000) - start_ms,
                            user_id=user_id,
                        )
                        await self._publish_event(result)
                        return result
        except Exception as exc:
            logger.warning("Module intent Tier 2 error: %s", exc)

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

        # Fallback
        result = IntentResult(
            intent="unknown",
            response="Извините, я не понял запрос. Попробуйте ещё раз.",
            action=None,
            source="fallback",
            latency_ms=int(time.time() * 1000) - start_ms,
            user_id=user_id,
        )
        await self._publish_event(result)
        return result

    def _get_system_prompt(self) -> str:
        """Build dynamic system prompt including current module registry and device types."""
        if self._system_prompt:
            return self._system_prompt

        base = (
            "You are Selena, a smart-home voice assistant by SelenaCore. "
            "Keep answers short — one or two sentences. "
            "CRITICAL: Reply in the SAME language as the user's message. "
            "If the user speaks Ukrainian — reply in Ukrainian. "
            "If the user speaks Russian — reply in Russian. "
            "NEVER default to English unless the user writes in English. "
        )

        # Try to enrich with module info
        try:
            from core.module_loader.sandbox import get_sandbox
            modules = get_sandbox().list_modules()
            if modules:
                module_list = ", ".join(m.name for m in modules if m.status == "RUNNING")
                base += f"Активные модули: {module_list}. "
        except Exception:
            pass

        return base

    def set_system_prompt(self, prompt: str) -> None:
        """Override the LLM system prompt."""
        self._system_prompt = prompt

    def refresh_system_prompt(self) -> None:
        """Clear cached prompt so it rebuilds on next call."""
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
