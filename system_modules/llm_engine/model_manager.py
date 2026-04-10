"""
system_modules/llm_engine/model_manager.py — LLM model manager

Manages model lifecycle across providers (Ollama local + cloud).
Reads active provider/model from unified voice config.
"""
from __future__ import annotations

import logging
import os

from core.config_writer import get_value, read_config

logger = logging.getLogger(__name__)


class ModelManager:
    """Manages LLM model lifecycle across providers."""

    def get_provider(self) -> str:
        return get_value("voice", "llm_provider", "ollama") or "ollama"

    def get_active(self) -> str:
        model = get_value("voice", "llm_model", "") or ""
        if not model:
            provider = self.get_provider()
            config = read_config()
            model = (
                config.get("voice", {}).get("providers", {}).get(provider, {}).get("model", "")
                or os.environ.get("OLLAMA_MODEL", "")
            )
        return model

    async def list_models(self) -> list[dict]:
        """Return installed models for the active provider."""
        provider = self.get_provider()
        active = self.get_active()

        if provider != "ollama":
            config = read_config()
            voice_cfg = config.get("voice", {})
            p_cfg = voice_cfg.get("providers", {}).get(provider, {})
            api_key = p_cfg.get("api_key", "")
            if not api_key:
                return []
            from system_modules.llm_engine.cloud_providers import list_models as cloud_list
            models = await cloud_list(provider, api_key)
            return [
                {"id": m.get("id", ""), "display_name": m.get("name", m.get("id", "")),
                 "installed": True, "active": m.get("id", "") == active}
                for m in models
            ]

        from system_modules.llm_engine.ollama_client import get_ollama_client
        client = get_ollama_client()
        try:
            installed = await client.list_models()
        except Exception:
            installed = []

        return [
            {"id": name, "display_name": name, "installed": True, "active": name == active}
            for name in installed
        ]

    async def download(self, model_id: str) -> bool:
        """Download a model via Ollama pull."""
        from system_modules.llm_engine.ollama_client import get_ollama_client
        client = get_ollama_client()
        logger.info("Downloading model: %s", model_id)
        return await client.pull_model(model_id)

    async def switch_model(self, model_id: str) -> bool:
        """Switch the active LLM model."""
        from system_modules.llm_engine.ollama_client import get_ollama_client
        client = get_ollama_client()

        installed = await client.list_models()
        if not any(model_id in name for name in installed):
            logger.error("Cannot switch to '%s': not installed", model_id)
            return False

        import system_modules.llm_engine.ollama_client as _mod
        _mod._client = None
        os.environ["OLLAMA_MODEL"] = model_id
        logger.info("Active model switched to '%s'", model_id)
        return True

    async def generate(self, prompt: str, system: str | None = None) -> str:
        """Generate using the active provider."""
        provider = self.get_provider()
        if provider == "ollama":
            from system_modules.llm_engine.ollama_client import get_ollama_client
            client = get_ollama_client()
            return await client.generate(prompt, system=system)
        else:
            config = read_config()
            voice_cfg = config.get("voice", {})
            p_cfg = voice_cfg.get("providers", {}).get(provider, {})
            api_key = p_cfg.get("api_key", "")
            model = p_cfg.get("model", "")
            if not api_key or not model:
                return ""
            from system_modules.llm_engine.cloud_providers import generate
            return await generate(provider, api_key, model, prompt, system)


_manager: ModelManager | None = None


def get_model_manager() -> ModelManager:
    global _manager
    if _manager is None:
        _manager = ModelManager()
    return _manager
