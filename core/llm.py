"""
core/llm.py — Unified LLM entry point for SelenaCore.

Every LLM call in the system goes through ``llm_call()``. One unified system
prompt (DB key ``system``) handles both intent classification and chat —
the LLM always returns an intent JSON, emitting ``intent="chat"`` for
freeform questions.

Prompt keys accepted by ``llm_call``:
    "intent"    — full intent classification path (JSON mode auto-enabled)
    "chat"      — alias for "intent", kept for call-site clarity
    "translate" — offline translation of custom prompts (separate key)

Core operates in English end-to-end: the voice pipeline pre-translates user
text to English via InputTranslator before calling the LLM, and post-
translates the LLM's English response via OutputTranslator right before TTS.
System prompts therefore contain NO "reply in X language" directives.

Internal callers (PatternGenerator) bypass the DB by passing ``system="..."``
directly — those prompts are English-only and NOT user-editable.

Usage:
    from core.llm import llm_call

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
    prompt_key: str = "intent",
    system: str | None = None,
    extra_context: str = "",
    temperature: float = 0.7,
    max_tokens: int = 512,
    timeout: float = 15.0,
    json_mode: bool | None = None,
    num_ctx: int = 4096,
) -> str:
    """Single entry point for all LLM calls.

    Args:
        user_msg:       The user/context message sent to the LLM.
        prompt_key:     Which system prompt to load from DB.
                        "intent"|"chat"|"translate" (chat is an alias for intent).
                        Ignored when ``system`` is provided.
        system:         Optional inline system prompt that overrides the DB
                        lookup. Used by internal callers (PatternGenerator)
                        for hardcoded English prompts that are not user-editable.
        extra_context:  Appended to the system prompt (e.g. intent catalog).
        temperature:    LLM sampling temperature.
        max_tokens:     Max tokens in response.
        timeout:        Timeout in seconds.
        json_mode:      Force structured JSON output (Ollama format=json,
                        OpenAI/Groq response_format, Gemini responseMimeType).
                        ``None`` means auto-enable for intent/chat keys.
                        Inline ``system`` callers must set this explicitly.
        num_ctx:        Ollama context window. Cloud providers manage this
                        themselves and ignore the value.

    Returns:
        LLM response text (stripped). Empty string on failure.
    """
    if json_mode is None:
        json_mode = (system is None and prompt_key in ("intent", "chat"))
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


def _get_assistant_name() -> str:
    """Return the English form of the wake-word name (e.g. "Selena").

    Preference order:
      1. ``voice.wake_word_en`` (explicit EN form, set by wizard or settings)
      2. ``voice.wake_word_model`` auto-transliterated if Cyrillic
      3. "Selena" fallback
    """
    from core.config_writer import read_config

    config = read_config()
    voice_cfg = config.get("voice", {})

    wake_en = (voice_cfg.get("wake_word_en") or "").strip()
    if wake_en:
        return wake_en.split()[-1].capitalize() if wake_en else "Selena"

    wake = (voice_cfg.get("wake_word_model") or "").strip()
    if wake:
        parts = wake.replace("_", " ").strip().split()
        native = parts[-1] if parts else "Selena"
        # If it contains Cyrillic, transliterate
        if any("\u0400" <= ch <= "\u04ff" for ch in native):
            from core.translit import cyrillic_to_latin
            return cyrillic_to_latin(native).capitalize() or "Selena"
        return native.capitalize()
    return "Selena"


async def _resolve_system_prompt(prompt_key: str, provider: str) -> str:
    """Load system prompt from DB by key, format template variables.

    Core operates in English, so every prompt is loaded from the ``en`` slot
    and the only template variable honoured is ``{name}`` (the wake-word
    English form). Since the unified ``system`` prompt now handles both
    intent classification and chat, ``intent`` and ``chat`` keys resolve
    to the same row.
    """
    from core.prompt_store import get_prompt_store

    store = get_prompt_store()
    name = _get_assistant_name()

    # NOTE: prompt templates may contain literal `{...}` (e.g. JSON examples
    # for the intent classifier), so str.format() raises KeyError on them.
    # Use plain str.replace() for variable substitution.
    def _subst(tpl: str, **vars: str) -> str:
        for k, v in vars.items():
            tpl = tpl.replace("{" + k + "}", v)
        return tpl

    if prompt_key in ("intent", "chat"):
        tpl = await store.get("en", "system")
        return _subst(tpl, name=name)

    if prompt_key == "translate":
        return await store.get("en", "translate_system")

    logger.warning("Unknown prompt_key: %s", prompt_key)
    return ""
