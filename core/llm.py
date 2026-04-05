"""
core/llm.py — Unified LLM entry point for SelenaCore.

Every LLM call in the system goes through ``llm_call()``.
System prompts are loaded from DB (PromptStore) by ``prompt_key``.
No hardcoded prompts — everything from DB on the TTS language.

Prompt keys:
    "chat"      — conversation (hidden_system/compact + user_instructions)
    "intent"    — intent classification (intent_system)
    "rephrase"  — TTS rephrase/generation (rephrase_system + rephrase_prompt)
    "translate"  — translation tasks (translate_system)
    "pattern"   — voice command pattern generation (pattern_system)

Usage:
    from core.llm import llm_call

    text = await llm_call("pause the music", prompt_key="rephrase", temperature=0.9)
    json_str = await llm_call(query, prompt_key="intent", extra_context=catalog)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import httpx

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
    extra_context: str = "",
    temperature: float = 0.7,
    max_tokens: int = 512,
    timeout: float = 10.0,
) -> str:
    """Single entry point for all LLM calls.

    Args:
        user_msg:       The user/context message sent to the LLM.
        prompt_key:     Which system prompt to load from DB.
                        "chat"|"intent"|"rephrase"|"translate"|"pattern"
        extra_context:  Appended to the system prompt (e.g. intent catalog).
        temperature:    LLM sampling temperature.
        max_tokens:     Max tokens in response.
        timeout:        Timeout in seconds.

    Returns:
        LLM response text (stripped). Empty string on failure.
    """
    try:
        provider, provider_cfg = _get_provider()
        system = await _resolve_system_prompt(prompt_key, provider)
        if extra_context:
            system += "\n" + extra_context

        # Log prompt to Live STT Monitor
        _live_log("llm_prompt", {
            "prompt_key": prompt_key,
            "provider": provider,
            "system_prompt": system,
            "user_prompt": user_msg,
        })

        result = await asyncio.wait_for(
            _call_provider(provider, provider_cfg, system, user_msg, temperature, max_tokens),
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

    if provider == "llamacpp":
        cfg["url"] = voice_cfg.get("llamacpp_url", "http://localhost:8081")
    elif provider not in ("ollama",):
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
) -> str:
    """Dispatch to the appropriate LLM provider."""
    if provider == "ollama":
        from system_modules.llm_engine.ollama_client import get_ollama_client
        return await get_ollama_client().generate(
            prompt=prompt, system=system, temperature=temperature,
        )

    if provider == "llamacpp":
        url = cfg.get("url", "http://localhost:8081")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                f"{url}/v1/chat/completions",
                json={
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            return (
                resp.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

    # Cloud provider
    api_key = cfg.get("api_key", "")
    model = cfg.get("model", "")
    if not api_key or not model:
        logger.warning("Cloud LLM: no api_key or model for provider=%s", provider)
        return ""
    from system_modules.llm_engine.cloud_providers import generate
    return await generate(
        provider, api_key, model, prompt, system, temperature=temperature,
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

    if prompt_key == "chat":
        # Cloud: hidden_system + user_instructions
        # Local: hidden_compact + user_instructions
        if provider in ("ollama", "llamacpp"):
            hidden = await store.get(tts_lang, "hidden_compact")
        else:
            hidden = await store.get(tts_lang, "hidden_system")
        try:
            hidden = hidden.format(name=name, lang=lang_name)
        except (KeyError, IndexError):
            pass
        user_instr = await store.get(tts_lang, "user_instructions")
        sep = " " if provider in ("ollama", "llamacpp") else "\n"
        return hidden + sep + (user_instr or "")

    if prompt_key == "intent":
        tpl = await store.get(tts_lang, "intent_system")
        try:
            return tpl.format(name=name, lang=lang_name)
        except (KeyError, IndexError):
            return tpl

    if prompt_key == "rephrase":
        tpl = await store.get(tts_lang, "rephrase_system")
        try:
            return tpl.format(lang_name=lang_name)
        except (KeyError, IndexError):
            return tpl

    if prompt_key == "translate":
        return await store.get(tts_lang, "translate_system")

    if prompt_key == "pattern":
        return await store.get(tts_lang, "pattern_system")

    logger.warning("Unknown prompt_key: %s", prompt_key)
    return ""
