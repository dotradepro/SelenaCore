"""
system_modules/llm_engine/ollama_client.py — Ollama REST client

Selena no longer manages the Ollama server. The user installs it themselves
(local on this machine, LAN, or a remote proxy) and wires the URL + optional
Bearer token through the wizard's "LLM Provider" step or the Engines tab.

Supports:
  - Any Ollama-compatible HTTP endpoint (local or remote)
  - Optional ``Authorization: Bearer <token>`` for proxied deployments
  - Streaming and non-streaming responses
  - Auto-disable when system RAM < 5GB
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator, Callable

import httpx

logger = logging.getLogger(__name__)

def _cfg(path: str, default=None):
    try:
        from core.config_writer import get_nested
        v = get_nested(path)
        if v is not None:
            return v
    except Exception:
        pass
    return default


def _resolve_ollama_url() -> str:
    """voice.providers.ollama.url (canonical) → legacy llm.ollama_url → default.

    The legacy key is read once on startup as a transition safeguard; the
    one-shot config migration in core/config.py (Commit 8) rewrites it to
    the new location. Env OLLAMA_URL overrides both.
    """
    env = os.environ.get("OLLAMA_URL")
    if env:
        return env
    new_key = _cfg("voice.providers.ollama.url")
    if new_key:
        return str(new_key)
    legacy = _cfg("llm.ollama_url")
    if legacy:
        return str(legacy)
    return "http://localhost:11434"


def _resolve_ollama_key() -> str | None:
    """Optional Bearer token for proxied / authenticated Ollama deployments."""
    env = os.environ.get("OLLAMA_API_KEY")
    if env:
        return env
    key = _cfg("voice.providers.ollama.api_key")
    return str(key) if key else None


OLLAMA_URL = _resolve_ollama_url()
DEFAULT_MODEL = os.environ.get(
    "OLLAMA_MODEL", str(_cfg("llm.default_model", "phi3:mini"))
)
REQUEST_TIMEOUT = float(
    os.environ.get("OLLAMA_TIMEOUT", str(_cfg("llm.timeout_sec", 30)))
)
RAM_THRESHOLD_GB = float(
    os.environ.get("OLLAMA_MIN_RAM_GB", str(_cfg("llm.min_ram_gb", 5.0)))
)


def _available_ram_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:
        return 999.0  # assume OK if psutil not available


def _should_use_llm() -> bool:
    ram_gb = _available_ram_gb()
    if ram_gb < RAM_THRESHOLD_GB:
        logger.warning(
            "LLM auto-disabled: available RAM %.1fGB < threshold %.1fGB",
            ram_gb, RAM_THRESHOLD_GB
        )
        return False
    return True


class OllamaClient:
    """Async Ollama API client."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = (base_url or _resolve_ollama_url()).rstrip("/")
        self.api_key = api_key if api_key is not None else _resolve_ollama_key()
        # Read model dynamically so switch_model() env-var override is
        # picked up when the singleton is re-created.
        # Priority: env OLLAMA_MODEL → voice.llm_model (UI selection)
        #         → voice.providers.ollama.model → llm.default_model
        if model is None:
            model = (
                os.environ.get("OLLAMA_MODEL")
                or str(_cfg("voice.llm_model", ""))
                or str(_cfg("voice.providers.ollama.model", ""))
                or str(_cfg("llm.default_model", "phi3:mini"))
            )
        self.model = model

    def _headers(self) -> dict[str, str]:
        """Attach Bearer header when api_key is set (remote / proxied Ollama)."""
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    async def probe(self) -> dict[str, Any]:
        """Rich availability check. Returns:

            {"reachable": bool, "auth_required": bool, "status": int | None,
             "error": str | None}

        is_available() is kept for callers that only need a boolean.
        """
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self.base_url}/api/tags",
                    headers=self._headers(),
                )
            if resp.status_code == 200:
                return {"reachable": True, "auth_required": False, "status": 200, "error": None}
            if resp.status_code in (401, 403):
                return {"reachable": True, "auth_required": True,
                        "status": resp.status_code, "error": "authentication required"}
            return {"reachable": False, "auth_required": False,
                    "status": resp.status_code, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"reachable": False, "auth_required": False,
                    "status": None, "error": str(e)}

    async def is_available(self) -> bool:
        """Check if Ollama server is running. Returns False on auth errors too
        (use probe() if you need to differentiate)."""
        info = await self.probe()
        return bool(info.get("reachable")) and not info.get("auth_required")

    async def list_models(self) -> list[str]:
        """Return list of installed model names."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.base_url}/api/tags",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            logger.error("Ollama list_models error: %s", e)
            return []

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        json_mode: bool = False,
        num_ctx: int = 4096,
    ) -> str:
        """Generate a completion (non-streaming) via /api/chat messages format."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": num_ctx,
                "top_p": 0.9,
                "repeat_penalty": 1.1,
            },
        }
        if json_mode:
            payload["format"] = "json"

        try:
            # Intent classification with a large catalog can take 30-60s
            # on low-power devices (Pi 5, Jetson Nano) during cold model
            # load.  Use a generous timeout that covers first-request load
            # plus actual generation.
            gen_timeout = max(REQUEST_TIMEOUT, 90.0)
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(gen_timeout, connect=10.0),
            ) as client:
                resp = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.json().get("message", {}).get("content", "").strip()
        except httpx.HTTPStatusError as e:
            logger.error(
                "Ollama generate HTTP error: %s %s — model=%s body=%s",
                e.response.status_code, e.response.reason_phrase,
                model or self.model, e.response.text[:200],
            )
            return ""
        except Exception as e:
            logger.error("Ollama generate error (%s): %s", type(e).__name__, e)
            return ""

    async def stream_generate(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Generate a streaming completion via /api/chat, yielding tokens."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature, "num_predict": 512},
        }

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT * 2) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/api/chat",
                    json=payload, headers=self._headers(),
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            token = data.get("message", {}).get("content", "")
                            if token:
                                yield token
                            if data.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error("Ollama stream_generate error: %s", e)

    async def pull_model(
        self,
        model_name: str,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Download a model from Ollama registry. Returns True on success."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=30.0)) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/api/pull",
                    json={"name": model_name}, headers=self._headers(),
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line:
                            try:
                                data = json.loads(line)
                                status = data.get("status", "")
                                logger.info("Pulling %s: %s", model_name, status)
                                if progress_cb and "total" in data and "completed" in data:
                                    progress_cb(int(data["completed"]), int(data["total"]))
                            except Exception:
                                pass
            logger.info("Model '%s' pulled successfully", model_name)
            return True
        except Exception as e:
            logger.error("Failed to pull model '%s': %s", model_name, e)
            return False


_client: OllamaClient | None = None


def get_ollama_client() -> OllamaClient:
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client


def reset_ollama_client() -> None:
    """Clear the singleton so the next get_ollama_client() rereads config.

    Called after /setup/llm/provider/select, /setup/llm/provider/apikey, or
    any config edit that touches voice.providers.ollama.*.
    """
    global _client
    _client = None
