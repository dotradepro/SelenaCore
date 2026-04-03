"""core.stt.factory — Create STT provider from config or auto-detect hardware."""
from __future__ import annotations

import logging
import os

from core.stt.base import STTProvider, STTResult

logger = logging.getLogger(__name__)


class _DummyProvider(STTProvider):
    """Fallback provider when no STT backend is available."""

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> STTResult:
        return STTResult()


def create_stt_provider(config: dict | None = None) -> STTProvider:
    """Create STT provider based on config or auto-detection.

    Config structure (from core.yaml):
        stt:
          provider: auto | whisper_cpp | faster_whisper | openai
          whisper_cpp:
            host: http://localhost:9000
          faster_whisper:
            model: small
            device: auto
            compute_type: auto
          openai:
            api_key: ...
            model: whisper-1
    """
    if config is None:
        config = _load_stt_config()

    provider = config.get("provider", "auto")

    if provider == "auto":
        return _auto_detect(config)
    elif provider == "whisper_cpp":
        return _create_whisper_cpp(config)
    elif provider == "faster_whisper":
        return _create_faster_whisper(config)
    elif provider == "openai":
        return _create_openai(config)
    else:
        logger.warning("Unknown STT provider '%s', falling back to auto-detect", provider)
        return _auto_detect(config)


def _auto_detect(config: dict) -> STTProvider:
    """Auto-detect best STT provider for current hardware.

    Priority:
    1. whisper.cpp server if running (Jetson / pre-configured)
    2. faster-whisper with CUDA if GPU available
    3. faster-whisper CPU
    """
    # 1. Check whisper.cpp server
    whisper_host = config.get("whisper_cpp", {}).get("host", "http://localhost:9000")
    if _is_whisper_cpp_running(whisper_host):
        logger.info("STT auto-detect: whisper.cpp server found at %s", whisper_host)
        return _create_whisper_cpp(config)

    # 2. Check faster-whisper availability
    try:
        import faster_whisper  # noqa: F401
        logger.info("STT auto-detect: faster-whisper available")
        return _create_faster_whisper(config)
    except ImportError:
        pass

    # No STT available
    logger.error(
        "No STT provider available. Install one of: "
        "faster-whisper (pip install faster-whisper) or "
        "whisper.cpp server (build with CUDA)."
    )
    return _DummyProvider()


def _is_whisper_cpp_running(host: str) -> bool:
    """Check if whisper.cpp server is reachable."""
    try:
        import httpx
        resp = httpx.get(f"{host}/", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


def _create_whisper_cpp(config: dict) -> STTProvider:
    from core.stt.whisper_cpp import WhisperCppProvider
    host = config.get("whisper_cpp", {}).get("host", "http://localhost:9000")
    return WhisperCppProvider(host=host)


def _create_faster_whisper(config: dict) -> STTProvider:
    from core.stt.faster_whisper import FasterWhisperProvider
    fw_cfg = config.get("faster_whisper", {})
    return FasterWhisperProvider(
        model=fw_cfg.get("model", "small"),
        device=fw_cfg.get("device", "auto"),
        compute_type=fw_cfg.get("compute_type", "auto"),
    )


def _create_openai(config: dict) -> STTProvider:
    from core.stt.openai_stt import OpenAIWhisperProvider
    oai_cfg = config.get("openai", {})
    api_key = oai_cfg.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OpenAI STT requires api_key in config or OPENAI_API_KEY env var")
    return OpenAIWhisperProvider(
        api_key=api_key,
        model=oai_cfg.get("model", "whisper-1"),
    )


def _load_stt_config() -> dict:
    """Load STT config from core.yaml."""
    try:
        from core.config_writer import read_config
        return read_config().get("stt", {})
    except Exception:
        return {}
