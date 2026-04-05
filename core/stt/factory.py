"""core.stt.factory — Create Vosk STT provider from config or auto-detect models."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from core.stt.base import STTProvider, STTResult

logger = logging.getLogger(__name__)

# Default models directory
DEFAULT_MODELS_DIR = "/var/lib/selena/models/vosk"


class _DummyProvider(STTProvider):
    """Fallback provider when no STT backend is available."""

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> STTResult:
        return STTResult()


def create_stt_provider(config: dict | None = None) -> STTProvider:
    """Create Vosk STT provider based on config or auto-detection.

    Config structure (from core.yaml):
        stt:
          provider: vosk | auto
          vosk:
            models_dir: /var/lib/selena/models/vosk
            active_model: vosk-model-small-en-us-0.15
    """
    if config is None:
        config = _load_stt_config()

    provider = config.get("provider", "auto")

    if provider in ("vosk", "auto"):
        return _create_vosk(config)
    else:
        logger.warning("Unknown STT provider '%s', falling back to auto-detect", provider)
        return _create_vosk(config)


def _create_vosk(config: dict) -> STTProvider:
    """Create VoskProvider from config or auto-detect model directory."""
    from core.stt.vosk_provider import VoskProvider

    vosk_cfg = config.get("vosk", {})
    models_dir = vosk_cfg.get("models_dir", DEFAULT_MODELS_DIR)
    active_model = vosk_cfg.get("active_model", "")

    # Determine language from config
    lang = _get_active_lang()

    # 1. Try explicit active_model
    if active_model:
        model_path = os.path.join(models_dir, active_model)
        if os.path.isdir(model_path):
            logger.info("STT: using configured Vosk model '%s' (lang=%s)", active_model, lang)
            p = VoskProvider(model_path=model_path, lang=lang)
            p.load_model()
            return p
        logger.warning("STT: configured model '%s' not found at %s", active_model, model_path)

    # 2. Auto-detect: scan models_dir for any model directory
    model_path = _find_model(models_dir, lang)
    if model_path:
        logger.info("STT: auto-detected Vosk model at %s (lang=%s)", model_path, lang)
        p = VoskProvider(model_path=model_path, lang=lang)
        p.load_model()
        return p

    # 3. No model found
    logger.error(
        "No Vosk model found. Download a model to %s. "
        "Models: https://alphacephei.com/vosk/models",
        models_dir,
    )
    return _DummyProvider()


def _find_model(models_dir: str, lang: str) -> str | None:
    """Find best Vosk model in models_dir for given language.

    Prefers models matching language code in directory name.
    Falls back to any model found.
    """
    if not os.path.isdir(models_dir):
        return None

    candidates: list[str] = []
    lang_candidates: list[str] = []

    for entry in os.listdir(models_dir):
        full = os.path.join(models_dir, entry)
        if not os.path.isdir(full):
            continue
        # Check if it looks like a Vosk model (has mfcc.conf or similar)
        if _is_vosk_model(full):
            candidates.append(full)
            # Check if model name contains language code
            name_lower = entry.lower()
            if lang in name_lower or f"-{lang}-" in name_lower or name_lower.startswith(lang):
                lang_candidates.append(full)

    # Prefer language-matched model
    if lang_candidates:
        # Prefer smaller models (sort by directory size proxy: name with "small"/"nano")
        for keyword in ("nano", "small", "base"):
            for c in lang_candidates:
                if keyword in c.lower():
                    return c
        return lang_candidates[0]

    if candidates:
        return candidates[0]

    return None


def _is_vosk_model(path: str) -> bool:
    """Check if directory contains a valid Vosk model."""
    # Vosk models typically contain mfcc.conf or am/final.mdl
    markers = ["mfcc.conf", "conf/mfcc.conf", "am/final.mdl", "graph/HCLG.fst"]
    for marker in markers:
        if os.path.exists(os.path.join(path, marker)):
            return True
    # Also check for ivector directory (common in Vosk models)
    if os.path.isdir(os.path.join(path, "ivector")):
        return True
    return False


def _get_active_lang() -> str:
    """Get active language from config (voice.tts.primary.lang or system.language)."""
    try:
        from core.config_writer import read_config
        cfg = read_config()
        # Try TTS primary voice language first
        lang = cfg.get("voice", {}).get("tts", {}).get("primary", {}).get("lang")
        if lang:
            return lang
        # Fall back to system language
        lang = cfg.get("system", {}).get("language")
        if lang:
            return lang
    except Exception:
        pass
    return "en"


def _load_stt_config() -> dict:
    """Load STT config from core.yaml."""
    try:
        from core.config_writer import read_config
        return read_config().get("stt", {})
    except Exception:
        return {}
