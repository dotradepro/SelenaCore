"""
system_modules/llm_engine/ollama_client.py — Ollama REST client

Supports:
  - Local inference via Ollama (http://localhost:11434)
  - Models: phi-3-mini, gemma-2b, qwen2.5:0.5b, llama3.2:1b
  - Streaming and non-streaming responses
  - Auto-disable when system RAM < 5GB
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import AsyncGenerator

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "phi3:mini")
REQUEST_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "30"))
RAM_THRESHOLD_GB = float(os.environ.get("OLLAMA_MIN_RAM_GB", "5.0"))


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
        self, base_url: str = OLLAMA_URL, model: str = DEFAULT_MODEL
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def is_available(self) -> bool:
        """Check if Ollama server is running."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """Return list of installed model names."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
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
    ) -> str:
        """Generate a completion (non-streaming) via /api/chat messages format."""
        if not _should_use_llm():
            return ""

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": 512},
        }

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json().get("message", {}).get("content", "").strip()
        except Exception as e:
            logger.error("Ollama generate error: %s", e)
            return ""

    async def stream_generate(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Generate a streaming completion via /api/chat, yielding tokens."""
        if not _should_use_llm():
            return

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
                    "POST", f"{self.base_url}/api/chat", json=payload
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

    async def pull_model(self, model_name: str) -> bool:
        """Download a model from Ollama registry. Returns True on success."""
        try:
            async with httpx.AsyncClient(timeout=600) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/api/pull", json={"name": model_name}
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line:
                            try:
                                data = json.loads(line)
                                logger.info("Pulling %s: %s", model_name, data.get("status", ""))
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
