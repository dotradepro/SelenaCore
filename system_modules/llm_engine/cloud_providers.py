"""
system_modules/llm_engine/cloud_providers.py — Cloud LLM provider adapters.

Supports: OpenAI, Anthropic, Google AI, Groq.
Each provider can validate API keys and list available models.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {
        "name": "Ollama (Local)",
        "needs_key": False,
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models_endpoint": "/models",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    },
    "anthropic": {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "models_endpoint": "/models",
        "auth_header": "x-api-key",
        "auth_prefix": "",
        "extra_headers": {"anthropic-version": "2023-06-01"},
    },
    "google": {
        "name": "Google AI",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "models_endpoint": "/models",
        "auth_via_param": "key",
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "models_endpoint": "/models",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    },
}


def get_provider_list() -> list[dict[str, Any]]:
    """Return list of all supported providers with metadata."""
    return [
        {"id": pid, "name": p["name"], "needs_key": p.get("needs_key", True)}
        for pid, p in PROVIDERS.items()
    ]


async def validate_api_key(provider: str, api_key: str) -> dict[str, Any]:
    """Validate an API key by calling the provider's models endpoint.

    Returns {"valid": True/False, "error": "..." if invalid}.
    """
    prov = PROVIDERS.get(provider)
    if not prov:
        return {"valid": False, "error": f"Unknown provider: {provider}"}
    if not prov.get("needs_key", True):
        return {"valid": True, "error": None}
    if not api_key:
        return {"valid": False, "error": "API key is required"}

    try:
        headers: dict[str, str] = {}
        params: dict[str, str] = {}

        if "auth_header" in prov:
            headers[prov["auth_header"]] = prov.get("auth_prefix", "") + api_key
        if "extra_headers" in prov:
            headers.update(prov["extra_headers"])
        if "auth_via_param" in prov:
            params[prov["auth_via_param"]] = api_key

        url = prov["base_url"] + prov["models_endpoint"]
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers, params=params)

        if resp.status_code == 200:
            return {"valid": True, "error": None}
        elif resp.status_code in (401, 403):
            return {"valid": False, "error": "Invalid API key"}
        else:
            return {"valid": False, "error": f"HTTP {resp.status_code}"}

    except httpx.ConnectError:
        return {"valid": False, "error": "Could not connect to provider"}
    except Exception as exc:
        logger.warning("API key validation failed for %s: %s", provider, exc)
        return {"valid": False, "error": str(exc)}


async def list_models(
    provider: str, api_key: str, text_only: bool = False,
) -> list[dict[str, str]]:
    """Fetch available models from a cloud provider.

    Returns [{"id": "model-id", "name": "display name"}, ...].

    ``text_only=True`` strips image-gen, TTS, STT, embedding and vision
    SKUs — the wizard uses this so the user's picker only shows chat
    models. The Engines tab leaves it off so power users see the full
    catalog.
    """
    prov = PROVIDERS.get(provider)
    if not prov or not prov.get("needs_key", True):
        return []
    if not api_key:
        return []

    try:
        headers: dict[str, str] = {}
        params: dict[str, str] = {}

        if "auth_header" in prov:
            headers[prov["auth_header"]] = prov.get("auth_prefix", "") + api_key
        if "extra_headers" in prov:
            headers.update(prov["extra_headers"])
        if "auth_via_param" in prov:
            params[prov["auth_via_param"]] = api_key

        url = prov["base_url"] + prov["models_endpoint"]
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

        return _parse_models(provider, data, text_only=text_only)

    except Exception as exc:
        logger.warning("Model listing failed for %s: %s", provider, exc)
        return []


# Substrings that identify non-text-generation SKUs. Matches are
# case-insensitive and applied after the provider-specific allowlist.
# Keeping this in one place makes it easy to add new families when
# providers launch them.
_TEXT_ONLY_BLOCKLIST: tuple[str, ...] = (
    "dall-e",
    "embedding",
    "whisper",
    "tts",
    "text-to-",
    "image",
    "babbage",
    "davinci",
    "moderation",
    "imagen",
    "veo",
    "chirp",
    "vision",
    "-audio",
    "-realtime",
)


def _is_text_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(marker in lowered for marker in _TEXT_ONLY_BLOCKLIST)


def _parse_models(
    provider: str, data: dict, text_only: bool = False,
) -> list[dict[str, str]]:
    """Parse provider-specific model list response into unified format."""
    models: list[dict[str, str]] = []

    if provider == "openai":
        for m in data.get("data", []):
            mid = m.get("id", "")
            if mid and ("gpt" in mid or "o1" in mid or "o3" in mid or "o4" in mid or "chatgpt" in mid):
                models.append({"id": mid, "name": mid})

    elif provider == "anthropic":
        for m in data.get("data", []):
            mid = m.get("id", "")
            if mid:
                name = m.get("display_name", mid)
                models.append({"id": mid, "name": name})

    elif provider == "google":
        for m in data.get("models", []):
            mid = m.get("name", "").replace("models/", "")
            if mid and "gemini" in mid:
                display = m.get("displayName", mid)
                models.append({"id": mid, "name": display})

    elif provider == "groq":
        for m in data.get("data", []):
            mid = m.get("id", "")
            if mid:
                models.append({"id": mid, "name": mid})

    if text_only:
        models = [m for m in models if _is_text_model(m["id"])]

    models.sort(key=lambda x: x["name"])
    return models


async def generate(
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
    system: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 512,
    json_mode: bool = False,
) -> str:
    """Unified generation across cloud providers.

    ``json_mode=True`` switches each provider to its native structured-output
    mode (OpenAI/Groq ``response_format``, Gemini ``responseMimeType``,
    Anthropic prompt-prefill).
    """
    prov = PROVIDERS.get(provider)
    if not prov or not api_key:
        return ""

    try:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if "auth_header" in prov:
            headers[prov["auth_header"]] = prov.get("auth_prefix", "") + api_key
        if "extra_headers" in prov:
            headers.update(prov["extra_headers"])

        if provider == "anthropic":
            messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
            # Anthropic has no JSON mode flag — prefill an opening brace so the
            # model is forced to continue with JSON.
            if json_mode:
                messages.append({"role": "assistant", "content": "{"})
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
            }
            if system:
                payload["system"] = system
            url = prov["base_url"] + "/messages"

        elif provider == "google":
            url = f"{prov['base_url']}/models/{model}:generateContent?key={api_key}"
            contents = [{"parts": [{"text": prompt}]}]
            # Use 8192 for thinking models (2.5-pro etc.) that consume tokens on reasoning
            gen_cfg: dict[str, Any] = {
                "temperature": temperature,
                "maxOutputTokens": 8192,
            }
            if json_mode:
                gen_cfg["responseMimeType"] = "application/json"
            payload: dict[str, Any] = {
                "contents": contents,
                "generationConfig": gen_cfg,
            }
            if system:
                payload["systemInstruction"] = {"parts": [{"text": system}]}
            headers.pop("Content-Type", None)

        else:  # openai / groq (OpenAI-compatible)
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            url = prov["base_url"] + "/chat/completions"

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        if provider == "anthropic":
            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text = block.get("text", "")
                    break
            else:
                text = data.get("content", [{}])[0].get("text", "")
            # Re-prepend the prefilled "{" we sent so the caller gets valid JSON
            if json_mode and text and not text.lstrip().startswith("{"):
                text = "{" + text
            return text
        elif provider == "google":
            # Gemini thinking models may have multiple parts; extract text parts only
            candidates = data.get("candidates", [])
            if not candidates:
                return ""
            parts = candidates[0].get("content", {}).get("parts", [])
            text_parts = [p.get("text", "") for p in parts if "text" in p and not p.get("thought")]
            if text_parts:
                return "\n".join(text_parts).strip()
            # Fallback: return any text part including thoughts
            all_text = [p.get("text", "") for p in parts if "text" in p]
            return "\n".join(all_text).strip() if all_text else ""
        else:
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    except Exception as exc:
        logger.error("Cloud LLM generation failed (%s): %s", provider, exc)
        return ""
