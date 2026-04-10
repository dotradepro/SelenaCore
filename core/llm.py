"""
core/llm.py — Unified LLM entry point for SelenaCore.

Every LLM call in the system goes through ``llm_call()``.
System prompts are loaded from DB (PromptStore) by ``prompt_key``.
No hardcoded prompts — everything from DB on the TTS language.

Prompt keys:
    "chat"      — conversation (hidden_system/compact + user_instructions)
    "intent"    — intent classification (intent_system)
    "rephrase"  — TTS rephrase/generation (rephrase_system)
    "translate" — translation tasks (translate_system)

Internal callers (PatternGenerator, wake-word variant generator) bypass the
DB by passing ``system="..."`` directly — those prompts must stay English-only
and are NOT user-editable.

Usage:
    from core.llm import llm_call

    text = await llm_call("pause the music", prompt_key="rephrase", temperature=0.9)
    json_str = await llm_call(query, prompt_key="intent", extra_context=catalog)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Live monitor callback ────────────────────────────────────────────────
_live_log_fn: Callable[[str, dict], None] | None = None


def set_live_log(fn: Callable[[str, dict], None] | None) -> None:
    """Set callback for Live STT Monitor: fn(event, data)."""
    global _live_log_fn
    _live_log_fn = fn


def _live_log(event: str, data: dict) -> None:
    if _live_log_fn:
        try:
            _live_log_fn(event, data)
        except Exception:
            pass


async def llm_call(
    user_msg: str,
    *,
    prompt_key: str = "rephrase",
    system: str | None = None,
    extra_context: str = "",
    temperature: float = 0.7,
    max_tokens: int = 512,
    timeout: float = 10.0,
    json_mode: bool | None = None,
    num_ctx: int = 4096,
) -> str:
    """Single entry point for all LLM calls.

    Args:
        user_msg:       The user/context message sent to the LLM.
        prompt_key:     Which system prompt to load from DB.
                        "chat"|"intent"|"rephrase"|"translate"
                        Ignored when ``system`` is provided.
        system:         Optional inline system prompt that overrides the DB
                        lookup. Used by internal callers (PatternGenerator,
                        wake-word variant generator) for hardcoded English
                        prompts that are not user-editable.
        extra_context:  Appended to the system prompt (e.g. intent catalog).
        temperature:    LLM sampling temperature.
        max_tokens:     Max tokens in response.
        timeout:        Timeout in seconds.
        json_mode:      Force structured JSON output (Ollama format=json,
                        OpenAI/Groq response_format, Gemini responseMimeType).
                        ``None`` means auto-enable for ``prompt_key="intent"``.
                        Inline ``system`` callers must set this explicitly.
        num_ctx:        Ollama context window. Cloud providers manage this
                        themselves and ignore the value.

    Returns:
        LLM response text (stripped). Empty string on failure.
    """
    if json_mode is None:
        json_mode = (system is None and prompt_key == "intent")
    try:
        provider, provider_cfg = _get_provider()
        if system is not None:
            resolved_system = system
        else:
            resolved_system = await _resolve_system_prompt(prompt_key, provider)
        if extra_context:
            resolved_system += "\n" + extra_context
        system = resolved_system

        # Log prompt to Live STT Monitor
        _live_log("llm_prompt", {
            "prompt_key": prompt_key,
            "provider": provider,
            "system_prompt": system,
            "user_prompt": user_msg,
            "json_mode": json_mode,
        })

        result = await asyncio.wait_for(
            _call_provider(
                provider, provider_cfg, system, user_msg,
                temperature, max_tokens,
                json_mode=json_mode, num_ctx=num_ctx,
            ),
            timeout=timeout,
        )

        result = result.strip().strip('"').strip("'")

        # Log response to Live STT Monitor
        _live_log("llm_raw", {
            "prompt_key": prompt_key,
            "provider": provider,
            "raw": result,
        })

        return result
    except asyncio.TimeoutError:
        logger.warning("LLM call timed out (key=%s, timeout=%s)", prompt_key, timeout)
        _live_log("llm_raw", {
            "prompt_key": prompt_key, "provider": "?", "raw": "[TIMEOUT]",
        })
        return ""
    except Exception as exc:
        logger.warning("LLM call failed (key=%s): %s", prompt_key, exc)
        _live_log("llm_raw", {
            "prompt_key": prompt_key, "provider": "?", "raw": f"[ERROR] {exc}",
        })
        return ""


# ── Provider resolution ──────────────────────────────────────────────────


def _get_provider() -> tuple[str, dict[str, Any]]:
    """Read LLM provider from config. Returns (provider_name, config_dict)."""
    from core.config_writer import read_config

    config = read_config()
    voice_cfg = config.get("voice", {})
    provider = voice_cfg.get("llm_provider", "ollama")

    cfg: dict[str, Any] = {"voice": voice_cfg}

    if provider == "ollama":
        # Resolve model: provider-specific override → top-level llm_model → env
        p_cfg = voice_cfg.get("providers", {}).get("ollama", {})
        cfg["model"] = (
            p_cfg.get("model")
            or voice_cfg.get("llm_model")
            or os.environ.get("OLLAMA_MODEL", "")
        )
    else:
        # Cloud provider — read api_key + model
        p_cfg = voice_cfg.get("providers", {}).get(provider, {})
        cfg["api_key"] = p_cfg.get("api_key", "")
        cfg["model"] = p_cfg.get("model", "")

    return provider, cfg


async def _call_provider(
    provider: str,
    cfg: dict[str, Any],
    system: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    *,
    json_mode: bool = False,
    num_ctx: int = 4096,
) -> str:
    """Dispatch to the appropriate LLM provider."""
    if provider == "ollama":
        from system_modules.llm_engine.ollama_client import get_ollama_client
        model = cfg.get("model") or None  # None → client falls back to its DEFAULT_MODEL
        return await get_ollama_client().generate(
            prompt=prompt, system=system, model=model,
            temperature=temperature, max_tokens=max_tokens,
            json_mode=json_mode, num_ctx=num_ctx,
        )

    # Cloud provider
    api_key = cfg.get("api_key", "")
    model = cfg.get("model", "")
    if not api_key or not model:
        logger.warning("Cloud LLM: no api_key or model for provider=%s", provider)
        return ""
    from system_modules.llm_engine.cloud_providers import generate
    return await generate(
        provider, api_key, model, prompt, system,
        temperature=temperature, max_tokens=max_tokens,
        json_mode=json_mode,
    )


# ── Prompt resolution ────────────────────────────────────────────────────


def _get_context() -> tuple[str, str, str, str]:
    """Return (assistant_name, lang_name, lang_code, tts_lang_code) from config."""
    from core.config_writer import read_config
    from core.lang_utils import lang_code_to_name

    config = read_config()
    voice_cfg = config.get("voice", {})
    sys_cfg = config.get("system", {})

    # Assistant name from wake phrase
    wake = voice_cfg.get("wake_word_model", "")
    if wake:
        parts = wake.replace("_", " ").strip().split()
        name = parts[-1].capitalize() if parts else "Selena"
    else:
        name = "Selena"

    # TTS language
    tts_lang = voice_cfg.get("tts", {}).get("primary", {}).get("lang", "")
    if not tts_lang:
        tts_lang = sys_cfg.get("language", "en")
    lang_name = lang_code_to_name(tts_lang)

    return name, lang_name, tts_lang, tts_lang


async def _resolve_system_prompt(prompt_key: str, provider: str) -> str:
    """Load system prompt from DB by key, format template variables."""
    from core.prompt_store import get_prompt_store

    store = get_prompt_store()
    name, lang_name, lang_code, tts_lang = _get_context()

    # NOTE: prompt templates may contain literal `{...}` (e.g. JSON examples
    # for the intent classifier), so str.format() raises KeyError on them.
    # Use plain str.replace() for variable substitution.
    def _subst(tpl: str, **vars: str) -> str:
        for k, v in vars.items():
            tpl = tpl.replace("{" + k + "}", v)
        return tpl

    if prompt_key == "chat":
        # Single system prompt for all providers (local + cloud)
        hidden = await store.get(tts_lang, "hidden_system")
        hidden = _subst(hidden, name=name, lang=lang_name)
        user_instr = await store.get(tts_lang, "user_instructions")
        return hidden + " " + (user_instr or "")

    if prompt_key == "intent":
        tpl = await store.get(tts_lang, "intent_system")
        return _subst(tpl, name=name, lang=lang_name)

    if prompt_key == "rephrase":
        tpl = await store.get(tts_lang, "rephrase_system")
        return _subst(tpl, lang_name=lang_name, lang=lang_name, name=name)

    if prompt_key == "translate":
        return await store.get(tts_lang, "translate_system")

    logger.warning("Unknown prompt_key: %s", prompt_key)
    return ""
