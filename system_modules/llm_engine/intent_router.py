"""
system_modules/llm_engine/intent_router.py — Multi-tier Intent Router

Tier 1:   Fast Matcher (keyword/regex YAML rules) — zero latency
Tier 1.5: System Module Intents (in-process regex) — microseconds
Tier 2:   Module Bus (WebSocket intent routing) — milliseconds
Tier 3:   LLM Intent Classification — any provider (Cloud/Ollama/llama.cpp)

Orchestration:
  1. Try Fast Matcher first
  2. Try system module registered intents (in-process, no HTTP)
  3. Try user module intents via Module Bus (WebSocket)
  4. Try LLM intent classification (active provider: cloud, ollama, or llamacpp)
  5. Fallback → "not understood"
  6. Publish voice.intent event to EventBus
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


@dataclass
class IntentResult:
    intent: str
    response: str
    action: dict[str, Any] | None
    source: str          # "fast_matcher" | "system_module" | "module_bus" | "llm" | "fallback"
    latency_ms: int
    user_id: str | None = None
    params: dict[str, Any] | None = None
    raw_llm: str | None = None  # raw LLM response before parsing (debug)


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
            await self._publish_event(result, raw_text=text)
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
            await self._publish_event(result, raw_text=text)
            return (result, steps) if trace else result

        # Tier 1.7: SmartMatcher — TF-IDF cosine similarity
        smart_result = None
        try:
            from system_modules.llm_engine.smart_matcher import get_smart_matcher
            from system_modules.llm_engine.structure_extractor import extract_structure
            matcher = get_smart_matcher()
            if matcher.is_built:
                struct = extract_structure(text)
                smart_result = matcher.match(text, struct)
        except Exception as exc:
            logger.debug("SmartMatcher Tier 1.7 error: %s", exc)

        if trace:
            steps.append({
                "tier": "1.7", "name": "SmartMatcher",
                "status": "hit" if smart_result else "miss",
                "ms": _elapsed(),
                "detail": smart_result["intent"] if smart_result else None,
                "score": smart_result.get("score") if smart_result else None,
            })

        if smart_result and not smart_result.get("uncertain"):
            result = IntentResult(
                intent=smart_result["intent"],
                response="",
                action=None,
                source="smart_matcher",
                latency_ms=_elapsed(),
                user_id=user_id,
                params=smart_result.get("params", {}),
            )
            await self._publish_event(result, raw_text=text)
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
                    await self._publish_event(result, raw_text=text)
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
                    await self._publish_event(result, raw_text=text)
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

        # Tier 3: LLM Intent Classification (any provider)
        llm_result = None
        llm_provider = ""
        llm_error = None
        try:
            llm_result, llm_provider = await self._try_llm_classification(text, lang)
        except asyncio.TimeoutError:
            llm_error = "timeout"
            logger.warning("LLM classification timeout for: %s", text[:50])
        except Exception as exc:
            llm_error = str(exc)
            logger.warning("LLM classification error: %s", exc)

        if trace:
            tier_name = f"LLM ({llm_provider})" if llm_provider else "LLM"
            if llm_result:
                steps.append({
                    "tier": "3", "name": tier_name,
                    "status": "hit",
                    "ms": _elapsed(),
                    "detail": llm_result.intent,
                })
            else:
                steps.append({
                    "tier": "3", "name": tier_name,
                    "status": "error" if llm_error else "skip",
                    "ms": _elapsed(),
                    "detail": llm_error or llm_provider or None,
                })

        if llm_result is not None:
            llm_result.latency_ms = _elapsed()
            llm_result.user_id = user_id
            await self._publish_event(llm_result, raw_text=text)
            return (llm_result, steps) if trace else llm_result

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
        await self._publish_event(result, raw_text=text)
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
        """Build (system_prompt, user_prompt) for LLM intent classification."""
        lang_names = {"uk": "Ukrainian", "en": "English", "de": "German", "fr": "French", "es": "Spanish"}
        lang_name = lang_names.get(lang, "English")
        catalog = self._build_intent_catalog(lang)

        # Load custom classification prompt from config, or use localized default
        rules_block = ""
        try:
            from core.config_writer import read_config
            rules_block = read_config().get("voice", {}).get("classification_prompt", "")
        except Exception:
            pass
        if not rules_block:
            try:
                from core.api.routes.voice_engines import _get_default_classification
                rules_block = _get_default_classification(lang)
            except Exception:
                from core.api.routes.voice_engines import DEFAULT_CLASSIFICATION_PROMPT
                rules_block = DEFAULT_CLASSIFICATION_PROMPT

        system_prompt = (
            f"User language: {lang_name} ({lang}).\n\n"
            f"Known intents:\n{catalog}\n\n"
            f"{rules_block}\n"
        )
        return system_prompt, text

    async def _try_llm_classification(self, text: str, lang: str) -> tuple[IntentResult | None, str]:
        """Attempt intent classification via the active LLM provider (cloud, ollama, or llamacpp).

        Returns (IntentResult | None, provider_name).
        """
        try:
            from core.config_writer import read_config
        except ImportError:
            return None, ""

        config = read_config()
        voice_cfg = config.get("voice", {})
        provider = voice_cfg.get("llm_provider", "ollama")

        # Two-step LLM: classify noun_class first, then extract intent
        if voice_cfg.get("llm_two_step"):
            return await self._two_step_llm(text, lang, voice_cfg, provider)

        system_prompt, user_prompt = self._build_classification_prompt(text, lang)

        # Local models need extra language enforcement (they tend to respond in English)
        if provider in ("ollama", "llamacpp"):
            lang_names = {"uk": "Ukrainian", "en": "English", "de": "German", "fr": "French", "es": "Spanish", "pl": "Polish"}
            lang_name = lang_names.get(lang, "English")
            system_prompt += f"\nCRITICAL: Response language for llm.response MUST be {lang_name}. Never use English unless user language is English."
            user_prompt = f"[{lang_name}] {user_prompt}"

        raw = ""

        if provider == "ollama":
            from system_modules.llm_engine.ollama_client import get_ollama_client
            # Read model from UI config (same logic as /llm/chat)
            model = voice_cfg.get("llm_model", os.environ.get("OLLAMA_MODEL", "phi3:mini"))
            p_model = voice_cfg.get("providers", {}).get("ollama", {}).get("model", "")
            if p_model:
                model = p_model
            client = get_ollama_client()
            if not await client.is_available():
                return None, "ollama (unavailable)"
            raw = await asyncio.wait_for(
                client.generate(prompt=user_prompt, system=system_prompt, model=model, temperature=0.0),
                timeout=25.0,
            )

        elif provider == "llamacpp":
            import httpx
            llamacpp_url = voice_cfg.get("llamacpp_url", "http://localhost:8081")
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            async with httpx.AsyncClient(timeout=25) as http:
                resp = await http.post(
                    f"{llamacpp_url}/v1/chat/completions",
                    json={"messages": messages, "temperature": 0.0, "max_tokens": 512},
                )
                resp.raise_for_status()
                raw = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")

        else:
            # Cloud provider (openai, anthropic, google, groq)
            providers_cfg = voice_cfg.get("providers", {})
            p_cfg = providers_cfg.get(provider, {})
            api_key = p_cfg.get("api_key", "")
            model = p_cfg.get("model", "")
            if not api_key or not model:
                return None, provider
            from system_modules.llm_engine.cloud_providers import generate
            raw = await asyncio.wait_for(
                generate(provider, api_key, model, user_prompt, system_prompt, temperature=0.0),
                timeout=15.0,
            )

        if not raw:
            return None, provider

        raw_debug = raw  # keep original for debug

        # Parse JSON — strip code fences if present
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
                source="llm", latency_ms=0, raw_llm=raw_debug,
            ), provider

        try:
            data = json.loads(cleaned[start_idx:end_idx + 1])
        except json.JSONDecodeError:
            return IntentResult(
                intent="llm.response", response=cleaned, action=None,
                source="llm", latency_ms=0, raw_llm=raw_debug,
            ), provider

        intent_name = data.get("intent", "")
        params = data.get("params") or {}
        response = data.get("response", "")

        known = self._get_known_intent_names()
        if intent_name and intent_name in known:
            return IntentResult(
                intent=intent_name, response=response, action=None,
                source="llm", latency_ms=0, params=params, raw_llm=raw_debug,
            ), provider
        elif intent_name == "llm.response" or response:
            return IntentResult(
                intent="llm.response", response=response, action=None,
                source="llm", latency_ms=0, params=params, raw_llm=raw_debug,
            ), provider

        return None, provider

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

    # ── Two-step LLM classification ──────────────────────────────────────

    async def _two_step_llm(
        self, text: str, lang: str, voice_cfg: dict, provider: str,
    ) -> tuple[IntentResult | None, str]:
        """Two-step LLM: Step 1 classifies noun_class, Step 2 extracts intent.

        Faster than single-step because each prompt is smaller:
        - Step 1: ~6 noun_classes → one-word answer (~100ms)
        - Step 2: ~5 intents within class → JSON answer (~400ms)
        Total: ~500ms vs ~1500ms for single-step with full catalog.
        """
        # Get noun_classes from IntentCompiler
        try:
            from system_modules.llm_engine.intent_compiler import get_intent_compiler
            compiler = get_intent_compiler()
            all_classes = compiler.get_all_noun_classes()
        except Exception:
            # Fallback to single-step if compiler unavailable
            return None, provider

        if not all_classes:
            return None, provider

        # Resolve model
        model = voice_cfg.get("llm_model", os.environ.get("OLLAMA_MODEL", "phi3:mini"))
        p_model = voice_cfg.get("providers", {}).get(provider, {}).get("model", "")
        if p_model:
            model = p_model

        # ── Step 1: Classify noun_class ──────────────────────────────────
        step1_system = (
            "You are a smart home intent classifier. "
            "Classify the user's utterance into exactly ONE category. "
            "Reply with ONLY the category name, nothing else."
        )
        step1_prompt = (
            f"Categories: {', '.join(all_classes)}, UNKNOWN\n"
            f"Utterance: \"{text}\"\n"
            f"Category:"
        )

        raw_class = await self._call_llm_provider(
            provider, voice_cfg, model, step1_prompt, step1_system,
        )
        if not raw_class:
            return None, provider

        # Robust parsing: find first word matching a known class
        noun_class = None
        for word in raw_class.upper().split():
            cleaned = word.strip(".,!?:\"'")
            if cleaned in all_classes:
                noun_class = cleaned
                break

        if not noun_class:
            logger.debug("Two-step LLM: Step 1 failed to classify, raw=%r", raw_class)
            return None, provider

        # ── Step 2: Extract intent within class ──────────────────────────
        class_intents = compiler.get_intents_for_noun_class(noun_class)
        if not class_intents:
            return None, provider

        lang_names = {
            "uk": "Ukrainian", "en": "English", "de": "German",
            "fr": "French", "es": "Spanish", "pl": "Polish",
        }
        lang_name = lang_names.get(lang, "English")

        step2_system = (
            f"You are a smart home assistant. Category: {noun_class}.\n"
            f"Allowed intents: {', '.join(class_intents)}\n"
            f"Reply with valid JSON only. Response language: {lang_name}.\n"
            f"Format: {{\"intent\": \"<from list>\", \"params\": {{}}, \"response\": \"<{lang_name} reply>\"}}"
        )
        step2_prompt = text

        raw_intent = await self._call_llm_provider(
            provider, voice_cfg, model, step2_prompt, step2_system,
        )
        if not raw_intent:
            return None, provider

        # Parse JSON response (reuse existing parsing logic)
        raw_debug = raw_intent
        cleaned = raw_intent.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        start_idx = cleaned.find("{")
        end_idx = cleaned.rfind("}")
        if start_idx == -1 or end_idx == -1:
            return IntentResult(
                intent="llm.response", response=cleaned, action=None,
                source="llm", latency_ms=0, raw_llm=raw_debug,
            ), provider

        try:
            data = json.loads(cleaned[start_idx:end_idx + 1])
        except json.JSONDecodeError:
            return IntentResult(
                intent="llm.response", response=cleaned, action=None,
                source="llm", latency_ms=0, raw_llm=raw_debug,
            ), provider

        intent_name = data.get("intent", "")
        params = data.get("params") or {}
        response = data.get("response", "")

        if intent_name and intent_name in class_intents:
            return IntentResult(
                intent=intent_name, response=response, action=None,
                source="llm", latency_ms=0, params=params, raw_llm=raw_debug,
            ), provider
        elif response:
            return IntentResult(
                intent="llm.response", response=response, action=None,
                source="llm", latency_ms=0, params=params, raw_llm=raw_debug,
            ), provider

        return None, provider

    async def _call_llm_provider(
        self,
        provider: str,
        voice_cfg: dict,
        model: str,
        prompt: str,
        system: str,
    ) -> str:
        """Call the active LLM provider and return raw string response."""
        try:
            if provider == "ollama":
                from system_modules.llm_engine.ollama_client import get_ollama_client
                client = get_ollama_client()
                if not await client.is_available():
                    return ""
                return await asyncio.wait_for(
                    client.generate(prompt=prompt, system=system, model=model, temperature=0.0),
                    timeout=15.0,
                )
            elif provider == "llamacpp":
                import httpx
                llamacpp_url = voice_cfg.get("llamacpp_url", "http://localhost:8081")
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ]
                async with httpx.AsyncClient(timeout=15) as http:
                    resp = await http.post(
                        f"{llamacpp_url}/v1/chat/completions",
                        json={"messages": messages, "temperature": 0.0, "max_tokens": 128},
                    )
                    resp.raise_for_status()
                    return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            else:
                # Cloud provider
                providers_cfg = voice_cfg.get("providers", {})
                p_cfg = providers_cfg.get(provider, {})
                api_key = p_cfg.get("api_key", "")
                cloud_model = p_cfg.get("model", model)
                if not api_key:
                    return ""
                from system_modules.llm_engine.cloud_providers import generate
                return await asyncio.wait_for(
                    generate(provider, api_key, cloud_model, prompt, system, temperature=0.0),
                    timeout=15.0,
                )
        except Exception as exc:
            logger.warning("Two-step LLM provider call failed: %s", exc)
            return ""

    async def _publish_event(self, result: IntentResult, raw_text: str = "") -> None:
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
                    "raw_text": raw_text,
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
