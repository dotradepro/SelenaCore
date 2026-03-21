"""
system_modules/llm_engine/model_manager.py — Ollama model manager

Manages model downloads, selection, and switching.
Maintains a list of recommended models with metadata.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

RECOMMENDED_MODELS = [
    {
        "id": "phi3:mini",
        "display_name": "Phi-3 Mini (3.8B)",
        "size_gb": 2.2,
        "ram_required_gb": 4.0,
        "languages": ["en", "ru"],
        "description": "Microsoft Phi-3 Mini — fast and capable for smart home",
    },
    {
        "id": "gemma2:2b",
        "display_name": "Gemma 2 2B",
        "size_gb": 1.6,
        "ram_required_gb": 3.0,
        "languages": ["en", "ru"],
        "description": "Google Gemma 2 2B — lightweight, multilingual",
    },
    {
        "id": "qwen2.5:0.5b",
        "display_name": "Qwen 2.5 0.5B",
        "size_gb": 0.4,
        "ram_required_gb": 1.5,
        "languages": ["en", "zh", "ru"],
        "description": "Qwen 2.5 0.5B — ultra-lightweight for low-RAM devices",
    },
    {
        "id": "llama3.2:1b",
        "display_name": "LLaMA 3.2 1B",
        "size_gb": 0.7,
        "ram_required_gb": 2.0,
        "languages": ["en"],
        "description": "Meta LLaMA 3.2 1B — small and fast",
    },
]


@dataclass
class ModelStatus:
    id: str
    display_name: str
    installed: bool
    active: bool
    size_gb: float
    ram_required_gb: float


class ModelManager:
    """Manages Ollama model lifecycle."""

    def __init__(self) -> None:
        self._active_model = os.environ.get("OLLAMA_MODEL", "phi3:mini")

    async def list_recommended(self) -> list[dict]:
        """Return recommended models with installation status."""
        from system_modules.llm_engine.ollama_client import get_ollama_client
        client = get_ollama_client()

        try:
            installed = await client.list_models()
        except Exception:
            installed = []

        result = []
        for m in RECOMMENDED_MODELS:
            installed_flag = any(m["id"] in installed_name for installed_name in installed)
            result.append({
                **m,
                "installed": installed_flag,
                "active": m["id"] == self._active_model,
            })
        return result

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

        self._active_model = model_id
        # Update the global client
        import system_modules.llm_engine.ollama_client as _mod
        _mod._client = None  # force re-init with new model
        os.environ["OLLAMA_MODEL"] = model_id
        logger.info("Active model switched to '%s'", model_id)
        return True

    def get_active(self) -> str:
        return self._active_model

    @staticmethod
    def check_ram_sufficient(model_id: str) -> bool:
        """Check if available RAM is sufficient for the given model."""
        try:
            import psutil
            available_gb = psutil.virtual_memory().available / (1024 ** 3)
        except ImportError:
            return True  # assume OK

        for m in RECOMMENDED_MODELS:
            if m["id"] == model_id:
                required = m["ram_required_gb"]
                if available_gb < required:
                    logger.warning(
                        "RAM check: available %.1fGB < required %.1fGB for %s",
                        available_gb, required, model_id
                    )
                    return False
                return True
        return True  # unknown model — let Ollama decide


_manager: ModelManager | None = None


def get_model_manager() -> ModelManager:
    global _manager
    if _manager is None:
        _manager = ModelManager()
    return _manager
