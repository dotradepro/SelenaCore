"""
core/api/routes/voice_engines.py — Voice engine management API.

Endpoints for:
  - Whisper STT model management
  - Piper binary install/uninstall/status + dynamic voice catalog
  - Ollama binary install/uninstall/start/stop/status
  - Cloud LLM provider management (key validation, model listing)
  - Model/voice download & delete

No module_token auth — localhost only, protected by iptables.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config_writer import get_value, read_config, update_config, update_many
from core.i18n import t

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/setup", tags=["voice-engines"])

CACHE_DIR = Path(os.environ.get("SELENA_CACHE_DIR", "/var/lib/selena/cache"))
PIPER_MODELS_DIR = Path(os.environ.get("PIPER_MODELS_DIR", "/var/lib/selena/models/piper"))


# ================================================================== #
#  Pydantic schemas                                                    #
# ================================================================== #

class EngineActionRequest(BaseModel):
    pass


class ModelIdRequest(BaseModel):
    model: str


class VoiceIdRequest(BaseModel):
    voice: str


class ProviderSelectRequest(BaseModel):
    provider: str


class ApiKeyRequest(BaseModel):
    provider: str
    api_key: str


class ProviderModelRequest(BaseModel):
    provider: str
    model: str


class OllamaModelRequest(BaseModel):
    model: str


class TtsSettingsRequest(BaseModel):
    length_scale: float = 1.0
    noise_scale: float = 0.667
    noise_w_scale: float = 0.8
    sentence_silence: float = 0.2
    volume: float = 1.0
    speaker: int = 0


class LlmChatRequest(BaseModel):
    text: str
    system: str | None = None


class SystemPromptRequest(BaseModel):
    prompt: str


class GpuOverrideRequest(BaseModel):
    force_cpu: bool


class SttTestRequest(BaseModel):
    device: str = "default"
    duration: int = 4


class SttTtsTestRequest(BaseModel):
    text: str
    voice: str | None = None
    output_device: str = "default"


# ================================================================== #
#  STT + TTS Test Flow                                                 #
# ================================================================== #

@router.post("/stt/test")
async def stt_test(req: SttTestRequest) -> dict[str, Any]:
    """Record audio from mic, run Whisper STT, return recognized text + audio as base64."""
    import base64
    import struct
    import wave
    import tempfile

    loop = asyncio.get_running_loop()
    input_device = req.device
    duration = min(req.duration, 10)  # max 10 seconds

    def _record() -> bytes:
        is_pulse = input_device and not input_device.startswith("hw:") and input_device != "default"
        if is_pulse:
            cmd = ["timeout", str(duration),
                   "parecord", "--raw", "--format=s16le", "--rate=16000",
                   "--channels=1", "--device=" + input_device]
        else:
            cmd = ["arecord", "-d", str(duration), "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw"]
            if input_device and input_device != "default":
                cmd += ["-D", input_device]
        result = subprocess.run(cmd, timeout=duration + 3, capture_output=True)
        return result.stdout

    try:
        raw = await asyncio.wait_for(loop.run_in_executor(None, _record), timeout=duration + 5)
        if len(raw) < 100:
            return {"status": "error", "text": "", "error": t("api.no_audio")}

        # Peak level
        samples = struct.unpack(f"<{len(raw) // 2}h", raw)
        peak = max(abs(s) for s in samples) / 32768.0 if samples else 0

        # STT via provider abstraction (Whisper)
        try:
            from core.stt import create_stt_provider
            provider = create_stt_provider()
            result = await provider.transcribe(raw, sample_rate=16000)
            text = result.text
            lang = result.lang
        except Exception as exc:
            return {"status": "error", "text": "", "error": t("api.stt_error", error=str(exc))}

        # Encode audio as base64 WAV for frontend playback
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(raw)
            audio_b64 = base64.b64encode(Path(tmp.name).read_bytes()).decode()
        finally:
            os.unlink(tmp.name)

        return {
            "status": "ok",
            "text": text,
            "lang": lang,
            "peak_level": round(peak, 4),
            "audio_b64": audio_b64,
            "duration": duration,
        }
    except Exception as exc:
        logger.error("STT test failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/tts/speak")
async def tts_speak(req: SttTtsTestRequest) -> Any:
    """Synthesize text via Piper TTS and return WAV audio."""
    from fastapi.responses import Response

    if not req.text.strip():
        raise HTTPException(status_code=422, detail=t("api.text_empty"))

    voice = req.voice or get_value("voice", "tts_voice", os.environ.get("PIPER_VOICE", "uk_UA-ukrainian_tts-medium"))

    try:
        from system_modules.voice_core.tts import TTSEngine
        engine = TTSEngine(voice=voice)
        wav_bytes = await engine.synthesize(req.text[:500])
        if not wav_bytes:
            raise HTTPException(status_code=503, detail=t("api.tts_failed"))

        # Also play on device if requested
        if req.output_device and req.output_device != "none":
            asyncio.create_task(_play_wav_on_device(wav_bytes, req.output_device))

        return Response(content=wav_bytes, media_type="audio/wav")
    except ImportError:
        raise HTTPException(status_code=503, detail=t("api.piper_not_installed"))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("TTS speak failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Hidden system prompt (auto-generated, NOT editable) ──────────────
# Injected: {name} from wake word, {lang} from STT model
HIDDEN_FULL = (
    "You are {name}. "
    "CRITICAL: Reply ONLY in {lang}. Every word MUST be in {lang}. "
    "Do NOT insert words from other languages in any combination. "
    "NEVER say you are AI, a language model, or neural network. "
    "NEVER mention model names, versions, or developers (Google, OpenAI, Meta, Anthropic, etc.). "
    "If asked who you are — say: I am {name}, your home assistant. "
    "If asked who created you — say: the SelenaCore team. "
    "Response will be read by TTS — plain text only, no markdown/URLs/emoji."
)

HIDDEN_COMPACT = (
    "You are {name}. {lang} only, no other languages. "
    "Never say you are AI or mention model names."
)

# ── Default intent classification prompt ─────────────────────────────
DEFAULT_CLASSIFICATION_PROMPT = (
    "You are a voice command classifier for a smart home assistant.\n"
    "Classify the user's voice command into one of the known intents.\n\n"
    "Rules:\n"
    '1. If the command matches a known intent, respond: {{"intent": "<name>", "params": {{<extracted params>}}}}\n'
    "2. Extract parameters when applicable (genre, station_name, query, level, etc.).\n"
    '3. If the command is a general question or conversation, respond: {{"intent": "llm.response", "params": {{}}, '
    '"response": "<helpful answer>"}}\n'
    "4. Output ONLY valid JSON. No markdown, no code fences, no explanation."
)

MAX_CLASSIFICATION_PROMPT = 600

# ── Default rephrase prompt ──────────────────────────────────────────
DEFAULT_REPHRASE_PROMPT = (
    "The system performed an action and generated a default response.\n"
    "Rephrase it naturally and concisely (1 sentence, no emoji, no markdown).\n"
    "Vary your phrasing — don't repeat the same structure.\n"
    "Keep it short for TTS. Plain text only."
)

MAX_REPHRASE_PROMPT = 400

# ── Default USER prompts loaded from config/prompts/{lang}.json ───────
_PROMPTS_DIR = Path(os.environ.get("SELENA_PROMPTS_DIR", "/opt/selena-core/config/prompts"))
_prompts_cache: dict[str, dict[str, str]] = {}


def _load_prompt_locale(lang: str) -> dict[str, str]:
    """Load prompt defaults from config/prompts/{lang}.json with cache."""
    if lang in _prompts_cache:
        return _prompts_cache[lang]
    path = _PROMPTS_DIR / f"{lang}.json"
    if path.is_file():
        try:
            import json as _json
            data = _json.loads(path.read_text(encoding="utf-8"))
            _prompts_cache[lang] = data
            return data
        except Exception:
            pass
    # Fallback to English
    if lang != "en":
        return _load_prompt_locale("en")
    # Hardcoded ultimate fallback
    return {"user_prompt": "Keep answers short and helpful.", "compact_user": "Short answers, plain text."}

# Character limits
MAX_USER_PROMPT = 300
MAX_COMPACT_USER = 120

# STT model name → language code mapping
_STT_LANG_MAP: dict[str, str] = {
    "uk": "Ukrainian",
    "ru": "Russian",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pl": "Polish",
    "pt": "Portuguese",
    "nl": "Dutch",
    "tr": "Turkish",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "ar": "Arabic",
    "hi": "Hindi",
    "cs": "Czech",
    "el": "Greek",
    "fi": "Finnish",
    "sv": "Swedish",
    "da": "Danish",
    "hu": "Hungarian",
    "ro": "Romanian",
    "sk": "Slovak",
    "bg": "Bulgarian",
    "hr": "Croatian",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "et": "Estonian",
    "sl": "Slovenian",
    "sr": "Serbian",
    "ca": "Catalan",
    "eu": "Basque",
    "gl": "Galician",
    "ka": "Georgian",
    "fa": "Persian",
    "he": "Hebrew",
    "id": "Indonesian",
    "ms": "Malay",
    "th": "Thai",
    "vi": "Vietnamese",
}


def _detect_lang_from_stt(stt_model: str) -> str:
    """Detect language from STT config. Whisper auto-detects."""
    if stt_model in ("tiny", "base", "small", "medium", "large"):
        return "Auto-detect (Whisper)"
    return "Auto-detect (Whisper)"


def _extract_name_from_wake(wake_phrase: str) -> str:
    """Extract assistant name from wake phrase: 'привіт_селена' → 'Селена'."""
    parts = wake_phrase.replace("_", " ").strip().split()
    # Name is the last word (after greeting like "привіт", "hello", "hey", etc.)
    if len(parts) >= 2:
        return parts[-1].capitalize()
    if len(parts) == 1:
        return parts[0].capitalize()
    return "Selena"


def _get_prompt_context() -> tuple[str, str, str]:
    """Return (assistant_name, response_language, ui_lang_code) from config.

    Response language is derived from UI language (system.language),
    NOT from STT model — there is no multilingual recognition.
    """
    config = read_config()
    voice_cfg = config.get("voice", {})
    sys_cfg = config.get("system", {})
    wake = voice_cfg.get("wake_word_model", "")
    name = _extract_name_from_wake(wake) if wake else "Selena"
    ui_lang = sys_cfg.get("language", "en")
    lang = _STT_LANG_MAP.get(ui_lang, "English")
    return name, lang, ui_lang


def _get_default_user_prompt(ui_lang: str) -> str:
    return _load_prompt_locale(ui_lang).get("user_prompt", "Keep answers short and helpful.")


def _get_default_compact_user(ui_lang: str) -> str:
    return _load_prompt_locale(ui_lang).get("compact_user", "Short answers, plain text.")


def _get_default_classification(ui_lang: str) -> str:
    return _load_prompt_locale(ui_lang).get("classification_prompt", DEFAULT_CLASSIFICATION_PROMPT)


def _get_default_rephrase(ui_lang: str) -> str:
    return _load_prompt_locale(ui_lang).get("rephrase_prompt", DEFAULT_REPHRASE_PROMPT)


def build_system_prompt(compact: bool = False) -> str:
    """Build the full prompt: hidden system part + user part.

    Args:
        compact: If True, use short prompt for local models (ollama/llamacpp).
    """
    config = read_config()
    voice_cfg = config.get("voice", {})
    name, lang, ui_lang = _get_prompt_context()

    if compact:
        hidden = HIDDEN_COMPACT.format(name=name, lang=lang)
        user = voice_cfg.get("compact_user_prompt", "") or _get_default_compact_user(ui_lang)
        return hidden + " " + user

    hidden = HIDDEN_FULL.format(name=name, lang=lang)
    user = voice_cfg.get("user_prompt", "") or _get_default_user_prompt(ui_lang)
    return hidden + "\n" + user


@router.get("/llm/system-prompt")
async def get_system_prompt() -> dict[str, Any]:
    """Get prompt settings: hidden preview + editable user prompts."""
    config = read_config()
    voice_cfg = config.get("voice", {})
    name, lang, ui_lang = _get_prompt_context()

    saved_user = voice_cfg.get("user_prompt", "")
    saved_compact = voice_cfg.get("compact_user_prompt", "")
    default_user = _get_default_user_prompt(ui_lang)
    default_compact = _get_default_compact_user(ui_lang)

    saved_classification = voice_cfg.get("classification_prompt", "")
    saved_rephrase = voice_cfg.get("rephrase_prompt", "")
    default_classification = _get_default_classification(ui_lang)
    default_rephrase = _get_default_rephrase(ui_lang)

    return {
        "name": name,
        "lang": lang,
        "ui_lang": ui_lang,
        "hidden_full": HIDDEN_FULL.format(name=name, lang=lang),
        "hidden_compact": HIDDEN_COMPACT.format(name=name, lang=lang),
        "user_prompt": saved_user or default_user,
        "is_custom_user": bool(saved_user),
        "default_user": default_user,
        "compact_user": saved_compact or default_compact,
        "is_custom_compact": bool(saved_compact),
        "default_compact": default_compact,
        "classification_prompt": saved_classification or default_classification,
        "is_custom_classification": bool(saved_classification),
        "default_classification": default_classification,
        "rephrase_prompt": saved_rephrase or default_rephrase,
        "is_custom_rephrase": bool(saved_rephrase),
        "default_rephrase": default_rephrase,
        "limits": {
            "user_prompt": MAX_USER_PROMPT,
            "compact_user": MAX_COMPACT_USER,
            "classification_prompt": MAX_CLASSIFICATION_PROMPT,
            "rephrase_prompt": MAX_REPHRASE_PROMPT,
        },
        "full_preview": build_system_prompt(compact=False),
        "compact_preview": build_system_prompt(compact=True),
    }


@router.post("/llm/user-prompt")
async def save_user_prompt(req: SystemPromptRequest) -> dict[str, Any]:
    """Save custom user prompt (cloud models)."""
    prompt = req.prompt.strip()[:MAX_USER_PROMPT]
    _, _, ui_lang = _get_prompt_context()
    if prompt == _get_default_user_prompt(ui_lang):
        prompt = ""
    update_config("voice", "user_prompt", prompt)
    return {"status": "ok"}


@router.post("/llm/compact-prompt")
async def save_compact_prompt(req: SystemPromptRequest) -> dict[str, Any]:
    """Save custom compact user prompt (local models)."""
    prompt = req.prompt.strip()[:MAX_COMPACT_USER]
    _, _, ui_lang = _get_prompt_context()
    if prompt == _get_default_compact_user(ui_lang):
        prompt = ""
    update_config("voice", "compact_user_prompt", prompt)
    return {"status": "ok"}


@router.post("/llm/classification-prompt")
async def save_classification_prompt(req: SystemPromptRequest) -> dict[str, Any]:
    """Save custom intent classification prompt."""
    prompt = req.prompt.strip()[:MAX_CLASSIFICATION_PROMPT]
    _, _, ui_lang = _get_prompt_context()
    if prompt == _get_default_classification(ui_lang):
        prompt = ""
    update_config("voice", "classification_prompt", prompt)
    # Clear intent router cached prompt
    try:
        from system_modules.llm_engine.intent_router import get_intent_router
        get_intent_router().refresh_system_prompt()
    except Exception:
        pass
    return {"status": "ok"}


@router.post("/llm/rephrase-prompt")
async def save_rephrase_prompt(req: SystemPromptRequest) -> dict[str, Any]:
    """Save custom rephrase prompt."""
    prompt = req.prompt.strip()[:MAX_REPHRASE_PROMPT]
    _, _, ui_lang = _get_prompt_context()
    if prompt == _get_default_rephrase(ui_lang):
        prompt = ""
    update_config("voice", "rephrase_prompt", prompt)
    return {"status": "ok"}


@router.post("/llm/system-prompt/reset")
async def reset_system_prompt() -> dict[str, Any]:
    """Reset user prompts to i18n defaults."""
    update_many([
        ("voice", "user_prompt", ""),
        ("voice", "compact_user_prompt", ""),
        ("voice", "classification_prompt", ""),
        ("voice", "rephrase_prompt", ""),
        ("voice", "system_prompt", ""),       # clean up old config keys
        ("voice", "compact_prompt", ""),
        ("voice", "tts_rules", ""),
    ])
    _, _, ui_lang = _get_prompt_context()
    return {
        "status": "ok",
        "user_prompt": _get_default_user_prompt(ui_lang),
        "compact_user": _get_default_compact_user(ui_lang),
    }


@router.post("/llm/rebuild")
async def rebuild_prompts() -> dict[str, Any]:
    """Rebuild prompts after any settings change (language, name, STT model, etc.).

    Call this after: language change, wake word change, user prompt save, STT model change.
    Clears caches so next LLM call uses fresh prompt.
    """
    # Clear prompt locale cache (language may have changed)
    _prompts_cache.clear()

    # Clear intent_router cached prompt
    try:
        from system_modules.llm_engine.intent_router import get_intent_router
        get_intent_router().refresh_system_prompt()
    except Exception:
        pass

    name, lang, ui_lang = _get_prompt_context()
    return {
        "status": "ok",
        "name": name,
        "lang": lang,
        "ui_lang": ui_lang,
        "full_preview": build_system_prompt(compact=False),
        "compact_preview": build_system_prompt(compact=True),
    }


@router.post("/llm/chat")
async def llm_chat(req: LlmChatRequest) -> dict[str, Any]:
    """Send text to active LLM provider and return response."""
    if not req.text.strip():
        raise HTTPException(status_code=422, detail=t("api.text_empty"))

    config = read_config()
    voice_cfg = config.get("voice", {})
    provider = voice_cfg.get("llm_provider", "ollama")

    # Response language from UI language (system.language)
    name, response_lang, _ = _get_prompt_context()

    # Local providers get compact prompt (small models lose focus with long prompts)
    is_local = provider in ("ollama", "llamacpp")
    system_prompt = req.system or build_system_prompt(compact=is_local)

    try:
        if provider == "ollama":
            ollama_url = voice_cfg.get("ollama_url", os.environ.get("OLLAMA_URL", "http://localhost:11434"))
            model = voice_cfg.get("llm_model", os.environ.get("OLLAMA_MODEL", "phi3:mini"))
            p_model = voice_cfg.get("providers", {}).get("ollama", {}).get("model", "")
            if p_model:
                model = p_model

            # Check if ollama is running
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(f"{ollama_url}/api/tags")
                    if resp.status_code != 200:
                        return {"status": "error", "response": "", "error": t("api.ollama_not_running"), "provider": provider}
            except Exception:
                return {"status": "error", "response": "", "error": t("api.ollama_not_available"), "provider": provider}

            # Use /api/chat (messages format) — models follow system prompt much better
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            # Language tag in user message — reinforces system prompt for small models
            user_text = f"[{response_lang}] {req.text}"
            messages.append({"role": "user", "content": user_text})

            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.7, "num_predict": 512},
            }

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{ollama_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
                response_text = data.get("message", {}).get("content", "").strip()

            return {"status": "ok", "response": response_text.lower(), "provider": provider, "model": model}

        elif provider == "llamacpp":
            llamacpp_url = voice_cfg.get("llamacpp_url", "http://localhost:8081")
            p_model = voice_cfg.get("providers", {}).get("llamacpp", {}).get("model", "")

            # Check if running
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(f"{llamacpp_url}/v1/models")
                    if resp.status_code != 200:
                        return {"status": "error", "response": "", "error": t("api.llamacpp_not_running"), "provider": provider}
            except Exception:
                return {"status": "error", "response": "", "error": t("api.llamacpp_not_available"), "provider": provider}

            # Use /v1/chat/completions (messages format) — models follow system prompt better
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            # Language tag in user message — reinforces system prompt for small models
            user_text = f"[{response_lang}] {req.text}"
            messages.append({"role": "user", "content": user_text})

            payload = {
                "messages": messages,
                "max_tokens": 512,
                "temperature": 0.7,
            }

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{llamacpp_url}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
                response_text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

            return {"status": "ok", "response": response_text.lower(), "provider": provider, "model": p_model}

        else:
            # Cloud provider
            providers_cfg = voice_cfg.get("providers", {})
            p_cfg = providers_cfg.get(provider, {})
            api_key = p_cfg.get("api_key", "")
            model = p_cfg.get("model", "")

            if not api_key:
                return {"status": "error", "response": "", "error": t("api.no_api_key", provider=provider), "provider": provider}
            if not model:
                return {"status": "error", "response": "", "error": t("api.no_model_selected", provider=provider), "provider": provider}

            from system_modules.llm_engine.cloud_providers import generate
            response_text = await generate(provider, api_key, model, req.text, system_prompt)

            if not response_text:
                return {"status": "error", "response": "", "error": t("api.llm_empty_response"), "provider": provider}

            return {"status": "ok", "response": response_text.lower(), "provider": provider, "model": model}

    except Exception as exc:
        logger.error("LLM chat failed: %s", exc)
        return {"status": "error", "response": "", "error": str(exc), "provider": provider}


@router.post("/tts/test")
async def tts_test(req: SttTtsTestRequest) -> dict[str, Any]:
    """Stream TTS: piper → paplay directly, no intermediate file."""
    import time as _time
    from system_modules.voice_core.tts import sanitize_for_tts, TTSSettings, _load_tts_settings

    if not req.text.strip():
        raise HTTPException(status_code=422, detail=t("api.text_empty"))

    voice = req.voice or get_value("voice", "tts_voice", os.environ.get("PIPER_VOICE", "uk_UA-ukrainian_tts-medium"))
    device = req.output_device or "default"
    # Resolve "default" to the configured output device
    if device == "default":
        device = get_value("voice", "audio_force_output", "") or "default"
    clean = sanitize_for_tts(req.text[:500])
    if not clean:
        raise HTTPException(status_code=422, detail="empty after sanitize")

    # Load per-voice settings based on which voice is being tested
    config = read_config()
    tts_cfg = config.get("voice", {}).get("tts", {})
    pri_voice = tts_cfg.get("primary", {}).get("voice", "")
    fb_voice = tts_cfg.get("fallback", {}).get("voice", "")
    if voice == fb_voice:
        voice_settings = tts_cfg.get("fallback", {}).get("settings", {})
    else:
        voice_settings = tts_cfg.get("primary", {}).get("settings", {})
    settings = TTSSettings(**voice_settings) if voice_settings else _load_tts_settings()

    t0 = _time.monotonic()
    loop = asyncio.get_event_loop()

    gpu_url = os.environ.get("PIPER_GPU_URL", "http://localhost:5100")
    pcm_data = None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{gpu_url}/synthesize/raw", json={
                "text": clean, "voice": voice,
                "length_scale": settings.length_scale,
                "noise_scale": settings.noise_scale,
                "noise_w_scale": settings.noise_w_scale,
                "sentence_silence": getattr(settings, 'sentence_silence', 0.2),
                "speaker": settings.speaker,
                "volume": settings.volume,
            })
            if resp.status_code == 200 and resp.content:
                pcm_data = resp.content
    except Exception:
        pass

    synth_ms = int((_time.monotonic() - t0) * 1000)

    if pcm_data:
        size_kb = round(len(pcm_data) / 1024, 1)
        sample_rate = 22050
        t1 = _time.monotonic()
        await loop.run_in_executor(None, _aplay_raw_pcm, pcm_data, device, sample_rate)
        play_ms = int((_time.monotonic() - t1) * 1000)
    else:
        # Fallback: local piper binary pipe → paplay (streaming)
        from system_modules.voice_core.tts import PIPER_BIN, MODELS_DIR
        model_path = str(Path(MODELS_DIR) / f"{voice}.onnx")
        t1 = _time.monotonic()
        size_kb = await loop.run_in_executor(
            None, _stream_piper_to_paplay, clean, model_path, settings, device,
        )
        synth_ms = 0  # can't separate synth/play in pipe mode
        play_ms = int((_time.monotonic() - t1) * 1000)

    total_ms = int((_time.monotonic() - t0) * 1000)
    return {
        "status": "ok",
        "synth_ms": synth_ms,
        "play_ms": play_ms,
        "total_ms": total_ms,
        "size_kb": size_kb,
        "voice": voice,
    }


def _aplay_raw_pcm(pcm_data: bytes, device: str, sample_rate: int = 22050) -> None:
    """Play raw PCM s16le mono via aplay (ALSA direct) with software volume.

    Prepends 150ms silence so aplay pipe has time to start before speech begins.
    """
    import struct

    # Prepend silence (150ms) — prevents aplay pipe from cutting the first syllable
    silence_samples = int(sample_rate * 0.15)
    silence_bytes = b'\x00\x00' * silence_samples
    pcm_data = silence_bytes + pcm_data

    # Apply software volume from config
    try:
        vol_cfg = get_value("voice", "output_volume")
        vol = max(0.0, min(1.5, int(vol_cfg) / 100.0)) if vol_cfg is not None else 1.0
    except Exception:
        vol = 1.0

    if abs(vol - 1.0) > 0.01:
        n = len(pcm_data) // 2
        samples = struct.unpack(f"<{n}h", pcm_data)
        pcm_data = struct.pack(f"<{n}h", *(
            max(-32768, min(32767, int(s * vol))) for s in samples
        ))

    cmd = ["aplay", "-t", "raw", "-f", "S16_LE", "-r", str(sample_rate), "-c", "1"]
    if device and device != "none" and device != "default":
        cmd.extend(["-D", device])
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.stdin.write(pcm_data)
        proc.stdin.close()
        proc.wait(timeout=120)
    except Exception as e:
        logger.warning("aplay playback error: %s", e)


def _play_raw_pcm(pcm_data: bytes, device: str) -> None:
    """Play raw PCM s16le 22050Hz mono via paplay."""
    cmd = ["paplay", "--raw", "--format=s16le", "--rate=22050", "--channels=1"]
    if device and device != "none" and device != "default":
        cmd.append("--device=" + device)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.stdin.write(pcm_data)
        proc.stdin.close()
        proc.wait(timeout=120)
    except Exception as e:
        logger.warning("PCM playback error: %s", e)
    finally:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass


def _stream_piper_to_paplay(text: str, model_path: str, settings, device: str) -> float:
    """Stream piper --output-raw | paplay --raw. Returns size in KB."""
    from system_modules.voice_core.tts import PIPER_BIN
    piper_cmd = [
        PIPER_BIN, "--model", model_path, "--output-raw",
        "--length-scale", str(settings.length_scale),
        "--noise-scale", str(settings.noise_scale),
        "--noise-w-scale", str(settings.noise_w_scale),
        "--sentence-silence", str(settings.sentence_silence),
        "--speaker", str(settings.speaker),
    ]
    play_cmd = ["paplay", "--raw", "--format=s16le", "--rate=22050", "--channels=1"]
    if device and device != "none" and device != "default":
        play_cmd.append("--device=" + device)

    piper_proc = play_proc = None
    try:
        piper_proc = subprocess.Popen(piper_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        play_proc = subprocess.Popen(play_cmd, stdin=piper_proc.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        piper_proc.stdout.close()
        piper_proc.stdin.write(text.encode("utf-8"))
        piper_proc.stdin.close()
        play_proc.wait(timeout=120)
        piper_proc.wait(timeout=5)
    except Exception as e:
        logger.warning("Stream piper error: %s", e)
    finally:
        for p in [piper_proc, play_proc]:
            if p:
                try:
                    p.kill()
                    p.wait(timeout=2)
                except Exception:
                    pass
    return 0


async def _play_wav_on_device(wav_bytes: bytes, device: str) -> None:
    """Play WAV bytes on output device (best-effort)."""
    import tempfile
    loop = asyncio.get_event_loop()

    def _play():
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            tmp.write(wav_bytes)
            tmp.close()
            is_pulse = device and not device.startswith("hw:") and device != "default"
            if is_pulse:
                cmd = ["paplay", "--device=" + device, tmp.name]
            else:
                cmd = ["aplay"]
                if device != "default":
                    cmd += ["-D", device]
                cmd.append(tmp.name)
            subprocess.run(cmd, timeout=10, capture_output=True)
        finally:
            os.unlink(tmp.name)

    try:
        await loop.run_in_executor(None, _play)
    except Exception as exc:
        logger.warning("Playback failed: %s", exc)


# ================================================================== #
#  Piper TTS Settings                                                  #
# ================================================================== #

TTS_DEFAULTS = {
    "length_scale": 1.0,
    "noise_scale": 0.667,
    "noise_w_scale": 0.8,
    "sentence_silence": 0.2,
    "volume": 1.0,
    "speaker": 0,
}


@router.get("/tts/dual-status")
async def tts_dual_status() -> dict[str, Any]:
    """Get dual-voice TTS status: primary + fallback voice info."""
    config = read_config()

    # New tts config format
    tts_cfg = config.get("voice", {}).get("tts", {})

    # Backward compat: old format → new
    if not tts_cfg:
        tts_cfg = {
            "primary": {
                "voice": config.get("voice", {}).get("tts_voice", "uk_UA-ukrainian_tts-medium"),
                "lang": "uk",
                "cuda": False,
                "settings": config.get("voice", {}).get("tts_settings", TTS_DEFAULTS),
            },
            "fallback": {
                "voice": config.get("voice", {}).get("tts_fallback_voice", "en_US-ryan-low"),
                "lang": "en",
                "cuda": False,
                "settings": TTS_DEFAULTS.copy(),
            },
        }

    result: dict[str, Any] = {"primary": {}, "fallback": {}}

    for role in ("primary", "fallback"):
        role_cfg = tts_cfg.get(role, {})
        voice_id = role_cfg.get("voice", "")
        lang = role_cfg.get("lang", "en" if role == "fallback" else "uk")
        cuda = role_cfg.get("cuda", False)
        settings = {**TTS_DEFAULTS, **role_cfg.get("settings", {})}

        # Check if model file exists
        model_exists = (PIPER_MODELS_DIR / f"{voice_id}.onnx").exists() if voice_id else False

        # Read model info
        num_speakers = 1
        sample_rate = 22050
        if voice_id:
            json_file = PIPER_MODELS_DIR / f"{voice_id}.onnx.json"
            if json_file.exists():
                try:
                    model_cfg = json.loads(json_file.read_text())
                    num_speakers = model_cfg.get("num_speakers", 1)
                    sample_rate = model_cfg.get("audio", {}).get("sample_rate", 22050)
                except Exception:
                    pass

        result[role] = {
            "voice": voice_id,
            "lang": lang,
            "cuda": cuda,
            "installed": model_exists,
            "num_speakers": num_speakers,
            "sample_rate": sample_rate,
            "settings": settings,
        }

    return result


@router.post("/tts/dual-config")
async def tts_dual_config_save(req: dict[str, Any]) -> dict[str, Any]:
    """Save dual-voice TTS config (primary + fallback)."""
    tts_cfg: dict[str, Any] = {}

    for role in ("primary", "fallback"):
        if role not in req:
            continue
        role_data = req[role]
        role_cfg: dict[str, Any] = {}
        if "voice" in role_data:
            role_cfg["voice"] = str(role_data["voice"])
        if "lang" in role_data:
            role_cfg["lang"] = str(role_data["lang"])
        if "cuda" in role_data:
            role_cfg["cuda"] = bool(role_data["cuda"])
        if "settings" in role_data:
            s = role_data["settings"]
            role_cfg["settings"] = {
                "length_scale": round(max(0.1, min(3.0, float(s.get("length_scale", 1.0)))), 2),
                "noise_scale": round(max(0.0, min(1.0, float(s.get("noise_scale", 0.667)))), 3),
                "noise_w_scale": round(max(0.0, min(1.0, float(s.get("noise_w_scale", 0.8)))), 3),
                "volume": round(max(0.1, min(3.0, float(s.get("volume", 1.0)))), 2),
                "speaker": int(s.get("speaker", 0)),
            }
        tts_cfg[role] = role_cfg

    update_config("voice", "tts", tts_cfg)
    return {"status": "ok"}


@router.post("/tts/test-mix")
async def tts_test_mix() -> dict[str, Any]:
    """Test dual-voice TTS with mixed language text — synthesize + play each segment."""
    import time as _time

    config = read_config()
    tts_cfg = config.get("voice", {}).get("tts", {})
    primary_lang = tts_cfg.get("primary", {}).get("lang", "uk")
    primary_voice = tts_cfg.get("primary", {}).get("voice", get_value("voice", "tts_voice", "uk_UA-ukrainian_tts-medium"))
    fallback_voice = tts_cfg.get("fallback", {}).get("voice", get_value("voice", "tts_fallback_voice", "en_US-amy-low"))

    test_texts = {
        "uk": "Привіт, я Селена. WiFi підключено. Status online.",
        "en": "Hello, I am Selena. Testing voice switching.",
    }
    test_text = test_texts.get(primary_lang, test_texts["en"])

    try:
        from system_modules.voice_core.tts import sanitize_for_tts
        from system_modules.voice_core.tts_preprocessor import split_by_language

        clean = sanitize_for_tts(test_text)
        segments = split_by_language(clean, primary_lang)
        segment_info = [{"text": s.text, "lang": s.lang} for s in segments]

        # Resolve output device
        device = get_value("voice", "audio_force_output", "") or "default"
        gpu_url = os.environ.get("PIPER_GPU_URL", "http://localhost:5100")
        loop = asyncio.get_event_loop()
        t0 = _time.monotonic()

        # Load per-voice settings
        pri_settings = tts_cfg.get("primary", {}).get("settings", {})
        fb_settings = tts_cfg.get("fallback", {}).get("settings", {})

        # Synthesize and play each segment with the correct voice + settings
        for seg in segments:
            is_fallback = seg.lang == "en"
            voice = fallback_voice if is_fallback else primary_voice
            s = fb_settings if is_fallback else pri_settings
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(f"{gpu_url}/synthesize/raw", json={
                        "text": seg.text, "voice": voice,
                        "length_scale": s.get("length_scale", 1.0),
                        "noise_scale": s.get("noise_scale", 0.667),
                        "noise_w_scale": s.get("noise_w_scale", 0.8),
                        "speaker": s.get("speaker", 0),
                        "volume": s.get("volume", 1.0),
                    })
                    if resp.status_code == 200 and resp.content:
                        sample_rate = int(resp.headers.get("X-Audio-Rate", "22050"))
                        await loop.run_in_executor(
                            None, _aplay_raw_pcm, resp.content, device, sample_rate,
                        )
            except Exception as exc:
                logger.warning("Test-mix segment failed (%s): %s", seg.lang, exc)

        total_ms = int((_time.monotonic() - t0) * 1000)

        return {
            "status": "ok",
            "test_text": test_text,
            "segments": segment_info,
            "primary_lang": primary_lang,
            "total_ms": total_ms,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.get("/tts/settings")
async def tts_settings_get() -> dict[str, Any]:
    """Get current Piper TTS synthesis settings."""
    config = read_config()
    saved = config.get("voice", {}).get("tts_settings", {})
    # Merge defaults with saved
    result = {**TTS_DEFAULTS, **saved}

    # Read num_speakers from active voice model config
    active_voice = get_value("voice", "tts_voice", os.environ.get("PIPER_VOICE", ""))
    num_speakers = 1
    if active_voice:
        json_file = PIPER_MODELS_DIR / f"{active_voice}.onnx.json"
        if json_file.exists():
            try:
                model_cfg = json.loads(json_file.read_text())
                num_speakers = model_cfg.get("num_speakers", 1)
                # Also return model defaults for reference
                inf = model_cfg.get("inference", {})
                result["model_defaults"] = {
                    "noise_scale": inf.get("noise_scale", 0.667),
                    "length_scale": inf.get("length_scale", 1.0),
                    "noise_w_scale": inf.get("noise_w", 0.8),
                }
            except Exception:
                pass

    result["num_speakers"] = num_speakers
    return result


@router.post("/tts/settings")
async def tts_settings_save(req: TtsSettingsRequest) -> dict[str, Any]:
    """Save Piper TTS synthesis settings."""
    settings = {
        "length_scale": round(max(0.1, min(3.0, req.length_scale)), 2),
        "noise_scale": round(max(0.0, min(1.0, req.noise_scale)), 3),
        "noise_w_scale": round(max(0.0, min(1.0, req.noise_w_scale)), 3),
        "sentence_silence": round(max(0.0, min(5.0, req.sentence_silence)), 2),
        "volume": round(max(0.1, min(3.0, req.volume)), 2),
        "speaker": max(0, req.speaker),
    }
    update_config("voice", "tts_settings", settings)
    return {"status": "ok", **settings}


# ================================================================== #
#  Hardware / GPU Status                                               #
# ================================================================== #

@router.get("/hardware/status")
async def hardware_status() -> dict[str, Any]:
    """Return hardware info including GPU detection."""
    from core.hardware import get_hardware_info
    info = get_hardware_info()

    # Check if Piper GPU server is running
    piper_gpu_url = os.environ.get("PIPER_GPU_URL", "http://localhost:5100")
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{piper_gpu_url}/health")
            if resp.status_code == 200:
                info["piper_gpu"] = True
    except Exception:
        pass
    if "piper_gpu" not in info:
        info["piper_gpu"] = False

    return info


@router.post("/hardware/gpu-override")
async def gpu_override(req: GpuOverrideRequest) -> dict[str, Any]:
    """Force CPU mode even on GPU hardware. Requires engine restart."""
    update_config("hardware", "force_cpu", req.force_cpu)
    # Clear cached detection so next call picks up override
    import core.hardware as hw
    hw._gpu_cache = None
    return {"status": "ok", "force_cpu": req.force_cpu, "restart_required": True}


# ================================================================== #
#  Install state tracking                                              #
# ================================================================== #

class _InstallState:
    """Track pip install/uninstall or shell install operations."""

    def __init__(self) -> None:
        self.running = False
        self.package = ""
        self.action = ""  # install | uninstall
        self.output: str = ""
        self.success: bool | None = None

    def reset(self) -> None:
        self.running = False
        self.package = ""
        self.action = ""
        self.output = ""
        self.success = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "package": self.package,
            "action": self.action,
            "output": self.output,
            "success": self.success,
        }


_piper_install = _InstallState()
_ollama_install = _InstallState()


# ================================================================== #
#  Whisper STT Status                                                  #
# ================================================================== #

@router.get("/whisper/status")
async def whisper_status() -> dict[str, Any]:
    """Check Whisper STT provider status."""
    try:
        from core.stt import create_stt_provider
        provider = create_stt_provider()
        provider_name = type(provider).__name__
        available = provider_name != "_DummyProvider"
        return {
            "available": available,
            "provider": provider_name,
            "auto_lang_detect": True,
            "supported_languages": 99,
        }
    except Exception as exc:
        return {"available": False, "provider": "none", "error": str(exc)}


# ================================================================== #
#  Piper Binary Management                                             #
# ================================================================== #

@router.get("/piper/status")
async def piper_status() -> dict[str, Any]:
    """Check if Piper TTS is available (native server on host or local binary)."""
    # Primary: check native Piper HTTP server on host
    gpu_url = os.environ.get("PIPER_GPU_URL", "http://localhost:5100")
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{gpu_url}/health")
            if resp.status_code == 200:
                data = resp.json()
                cuda = data.get("cuda", False)
                mode = "GPU" if cuda else "CPU"
                return {
                    "installed": True,
                    "version": f"native ({mode})",
                    "path": gpu_url,
                }
    except Exception:
        pass

    # Fallback: check local piper binary in container
    piper_bin = shutil.which("piper")
    version = None
    try:
        result = subprocess.run(
            ["pip", "show", "piper-tts"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass

    if piper_bin or version:
        return {"installed": True, "version": version or "unknown", "path": piper_bin}

    return {"installed": False, "version": None}


@router.post("/piper/install")
async def piper_install() -> dict[str, Any]:
    """Install Piper TTS — native server on host, no container install needed."""
    # Check if native server is already running
    gpu_url = os.environ.get("PIPER_GPU_URL", "http://localhost:5100")
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{gpu_url}/health")
            if resp.status_code == 200:
                return {"status": "ok", "done": True, "message": "Piper TTS server is already running natively on host"}
    except Exception:
        pass
    # Fallback: pip install inside container
    if _piper_install.running:
        return {"status": "already_running", **_piper_install.to_dict()}
    _piper_install.reset()
    _piper_install.running = True
    _piper_install.package = "piper-tts"
    _piper_install.action = "install"
    asyncio.create_task(_pip_action(_piper_install, "install", "piper-tts pathvalidate"))
    return {"status": "started"}


@router.post("/piper/uninstall")
async def piper_uninstall() -> dict[str, Any]:
    """Uninstall Piper TTS via pip."""
    if _piper_install.running:
        return {"status": "already_running", **_piper_install.to_dict()}
    _piper_install.reset()
    _piper_install.running = True
    _piper_install.package = "piper-tts"
    _piper_install.action = "uninstall"
    asyncio.create_task(_pip_action(_piper_install, "uninstall", "piper-tts pathvalidate"))
    return {"status": "started"}


@router.get("/piper/install-progress")
async def piper_install_progress() -> dict[str, Any]:
    """Poll Piper install/uninstall progress."""
    return _piper_install.to_dict()


# ================================================================== #
#  Ollama Binary Management                                            #
# ================================================================== #

@router.get("/ollama/status")
async def ollama_status() -> dict[str, Any]:
    """Check if Ollama is installed and running."""
    ollama_bin = shutil.which("ollama")
    installed = ollama_bin is not None
    version = None
    running = False

    if installed:
        try:
            result = subprocess.run(
                ["ollama", "--version"], capture_output=True, text=True, timeout=5
            )
            version = result.stdout.strip().replace("ollama version ", "") or "unknown"
        except Exception:
            version = "unknown"

    # Check if server is running
    ollama_url = get_value("voice", "ollama_url", os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            running = resp.status_code == 200
    except Exception:
        running = False

    return {"installed": installed, "version": version, "running": running, "url": ollama_url}


@router.post("/ollama/install")
async def ollama_install() -> dict[str, Any]:
    """Install Ollama via official install script."""
    if _ollama_install.running:
        return {"status": "already_running", **_ollama_install.to_dict()}
    _ollama_install.reset()
    _ollama_install.running = True
    _ollama_install.package = "ollama"
    _ollama_install.action = "install"
    asyncio.create_task(_shell_action(
        _ollama_install,
        ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
        timeout=300,
    ))
    return {"status": "started"}


@router.post("/ollama/uninstall")
async def ollama_uninstall() -> dict[str, Any]:
    """Uninstall Ollama."""
    if _ollama_install.running:
        return {"status": "already_running", **_ollama_install.to_dict()}
    _ollama_install.reset()
    _ollama_install.running = True
    _ollama_install.package = "ollama"
    _ollama_install.action = "uninstall"
    asyncio.create_task(_shell_action(
        _ollama_install,
        ["bash", "-c", "systemctl stop ollama 2>/dev/null; rm -f /usr/local/bin/ollama; systemctl disable ollama 2>/dev/null; rm -f /etc/systemd/system/ollama.service"],
        timeout=30,
    ))
    return {"status": "started"}


@router.post("/ollama/start")
async def ollama_start() -> dict[str, Any]:
    """Start Ollama server (systemd service or Docker container)."""
    loop = asyncio.get_event_loop()

    def _start():
        # Use nsenter to control host systemd (from inside container)
        if shutil.which("nsenter"):
            result = subprocess.run(
                ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--",
                 "systemctl", "start", "ollama"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return "started via systemd (host)"

        # Fallback: direct systemctl (if running on host)
        if shutil.which("systemctl"):
            result = subprocess.run(
                ["systemctl", "start", "ollama"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return "started via systemd"

        # Fallback: start local process
        ollama_bin = shutil.which("ollama")
        if not ollama_bin:
            raise RuntimeError("Ollama not installed")
        env = os.environ.copy()
        env["OLLAMA_HOST"] = "0.0.0.0:11434"
        from core.hardware import should_use_gpu
        env["OLLAMA_NUM_GPU"] = "999" if should_use_gpu() else "0"
        subprocess.Popen(
            [ollama_bin, "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        import time
        time.sleep(2)
        return "started as background process"

    try:
        msg = await loop.run_in_executor(None, _start)
        return {"status": "ok", "message": msg}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/ollama/stop")
async def ollama_stop() -> dict[str, Any]:
    """Stop Ollama server (Docker container or local process)."""
    loop = asyncio.get_event_loop()

    def _stop():
        # Use nsenter to control host systemd (from inside container)
        if shutil.which("nsenter"):
            result = subprocess.run(
                ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--",
                 "systemctl", "stop", "ollama"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return "stopped via systemd (host)"

        # Fallback: direct systemctl
        if shutil.which("systemctl"):
            result = subprocess.run(
                ["systemctl", "stop", "ollama"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return "stopped via systemd"

        # Fallback: kill process
        import signal
        try:
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    cmdline = (entry / "cmdline").read_bytes().decode(errors="ignore")
                    if "ollama" in cmdline and "serve" in cmdline:
                        os.kill(int(entry.name), signal.SIGTERM)
                except (PermissionError, FileNotFoundError, ProcessLookupError):
                    continue
        except Exception:
            pass
        return "killed process"

    try:
        msg = await loop.run_in_executor(None, _stop)
        return {"status": "ok", "message": msg}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/ollama/install-progress")
async def ollama_install_progress() -> dict[str, Any]:
    """Poll Ollama install/uninstall progress."""
    return _ollama_install.to_dict()


@router.get("/ollama/models")
async def ollama_models() -> dict[str, Any]:
    """List models — from Ollama API if running, fallback to disk scan."""
    ollama_url = get_value("voice", "ollama_url", os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    installed: list[dict] = []

    # Try Ollama API first
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            for m in data.get("models", []):
                size_gb = round(m.get("size", 0) / (1024 ** 3), 1)
                installed.append({
                    "id": m["name"],
                    "name": m["name"],
                    "size_gb": size_gb,
                    "installed": True,
                })
    except Exception:
        pass

    # Fallback: scan disk manifests (works when Ollama is stopped)
    if not installed:
        installed = _scan_ollama_models_from_disk()

    return {"models": installed}


def _scan_ollama_models_from_disk() -> list[dict]:
    """Scan Ollama model manifests on disk."""
    models = []
    manifests_root = None
    search_paths = []
    env_dir = os.environ.get("OLLAMA_MODELS_DIR")
    if env_dir:
        search_paths.append(Path(env_dir))
    search_paths += [
        Path("/usr/share/ollama/.ollama/models"),
        Path(os.path.expanduser("~/.ollama/models")),
        Path("/root/.ollama/models"),
    ]
    for base in search_paths:
        candidate = base / "manifests" / "registry.ollama.ai"
        if candidate.is_dir():
            manifests_root = candidate
            break
    if manifests_root is None:
        return models

    for namespace in manifests_root.iterdir():
        if not namespace.is_dir():
            continue
        for model_dir in namespace.iterdir():
            if not model_dir.is_dir():
                continue
            for tag_file in model_dir.iterdir():
                if not tag_file.is_file():
                    continue
                name = f"{model_dir.name}:{tag_file.name}" if namespace.name == "library" else f"{namespace.name}/{model_dir.name}:{tag_file.name}"
                # Get size from manifest
                size_gb = 0.0
                try:
                    manifest = json.loads(tag_file.read_text())
                    for layer in manifest.get("layers", []):
                        if layer.get("mediaType", "") == "application/vnd.ollama.image.model":
                            size_gb = round(layer.get("size", 0) / (1024 ** 3), 1)
                except Exception:
                    pass
                models.append({
                    "id": name,
                    "name": name,
                    "size_gb": size_gb,
                    "installed": True,
                })
    return models


# Curated list of popular small models for edge devices
_CURATED_MODELS = [
    {"id": "gemma3:1b", "name": "Gemma 3 1B (Google)", "size_gb": 0.8},
    {"id": "gemma3:4b", "name": "Gemma 3 4B (Google)", "size_gb": 3.3},
    {"id": "qwen2.5:0.5b", "name": "Qwen 2.5 0.5B", "size_gb": 0.4},
    {"id": "qwen2.5:1.5b", "name": "Qwen 2.5 1.5B", "size_gb": 1.0},
    {"id": "qwen2.5:3b", "name": "Qwen 2.5 3B", "size_gb": 1.9},
    {"id": "llama3.2:1b", "name": "LLaMA 3.2 1B (Meta)", "size_gb": 0.7},
    {"id": "llama3.2:3b", "name": "LLaMA 3.2 3B (Meta)", "size_gb": 2.0},
    {"id": "phi4-mini", "name": "Phi-4 Mini 3.8B (Microsoft)", "size_gb": 2.5},
    {"id": "smollm2:135m", "name": "SmolLM2 135M", "size_gb": 0.1},
    {"id": "smollm2:360m", "name": "SmolLM2 360M", "size_gb": 0.2},
    {"id": "smollm2:1.7b", "name": "SmolLM2 1.7B", "size_gb": 1.0},
    {"id": "tinyllama", "name": "TinyLlama 1.1B", "size_gb": 0.6},
    {"id": "ministral-3:3b", "name": "Ministral 3 3B (Mistral)", "size_gb": 2.2},
    {"id": "ministral-3:8b", "name": "Ministral 3 8B (Mistral)", "size_gb": 4.9},
    {"id": "deepseek-r1:1.5b", "name": "DeepSeek R1 1.5B", "size_gb": 1.1},
    {"id": "deepseek-r1:7b", "name": "DeepSeek R1 7B", "size_gb": 4.7},
]


@router.get("/llm/catalog")
async def llm_model_catalog() -> dict[str, Any]:
    """Return LLM models suitable for this device (filtered by RAM)."""
    ram_gb = 0
    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        ram_gb = 4

    max_size_gb = ram_gb * 0.75

    # Start with curated list + merge from Ollama registry
    models = list(_CURATED_MODELS)
    seen_ids = {m["id"] for m in models}

    # Try to fetch additional from Ollama registry
    cache_file = CACHE_DIR / "ollama_catalog.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    import time as _time
    remote_models: list[dict] = []

    if cache_file.exists() and (_time.time() - cache_file.stat().st_mtime) < 3600:
        try:
            remote_models = json.loads(cache_file.read_text()).get("models", [])
        except Exception:
            pass

    if not remote_models:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get("https://ollama.com/api/tags")
                resp.raise_for_status()
                data = resp.json()
            for m in data.get("models", []):
                size_gb = round(m.get("size", 0) / (1024 ** 3), 1)
                remote_models.append({"id": m["name"], "name": m["name"], "size_gb": size_gb})
            cache_file.write_text(json.dumps({"models": remote_models}, ensure_ascii=False))
        except Exception:
            pass

    for m in remote_models:
        if m["id"] not in seen_ids:
            models.append(m)
            seen_ids.add(m["id"])

    # Filter by RAM
    suitable = [m for m in models if 0 < m["size_gb"] <= max_size_gb]
    suitable.sort(key=lambda x: x["size_gb"])

    return {
        "models": suitable,
        "ram_total_gb": round(ram_gb, 1),
        "max_model_gb": round(max_size_gb, 1),
    }


# ================================================================== #
#  llama.cpp Server Management                                         #
# ================================================================== #

@router.get("/llamacpp/status")
async def llamacpp_status() -> dict[str, Any]:
    """Check if llama.cpp server is running."""
    llamacpp_url = get_value("voice", "llamacpp_url", "http://localhost:8081")
    running = False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{llamacpp_url}/v1/models")
            running = resp.status_code == 200
    except Exception:
        pass
    return {"running": running, "url": llamacpp_url}


LLAMACPP_START_SCRIPT = os.environ.get("LLAMACPP_START_SCRIPT", "")


def _run_on_host(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Run command on host via nsenter (PID 1 namespace)."""
    return subprocess.run(
        ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--"] + cmd,
        capture_output=True, text=True, timeout=timeout,
    )


@router.post("/llamacpp/start")
async def llamacpp_start(body: dict[str, Any] = {}) -> dict[str, Any]:
    """Start llama.cpp server natively on host."""
    model = body.get("model", "")
    if not model:
        config = read_config()
        voice_cfg = config.get("voice", {})
        p_cfg = voice_cfg.get("providers", {}).get("llamacpp", {})
        model = p_cfg.get("model", "")

    if not model:
        raise HTTPException(status_code=422, detail=t("api.no_model_specified"))

    gguf_path = await _find_gguf_for_model(model)
    if not gguf_path:
        raise HTTPException(status_code=404, detail=t("api.gguf_not_found", model=model))

    llamacpp_url = get_value("voice", "llamacpp_url", "http://localhost:8081")
    port = llamacpp_url.rsplit(":", 1)[-1] if ":" in llamacpp_url else "8081"

    from core.hardware import should_use_gpu
    n_gpu = "999" if should_use_gpu() else "0"

    loop = asyncio.get_event_loop()

    def _start():
        # Kill any existing llama.cpp server
        _run_on_host(["pkill", "-f", "llama_cpp.server"], timeout=5)
        import time
        time.sleep(1)

        # Start on host via nsenter as the host user who owns the script
        host_user = os.environ.get("HOST_USER", "root")
        run_cmd = ["bash", "-c"] if host_user == "root" else ["runuser", "-u", host_user, "--", "bash", "-c"]
        _run_on_host(run_cmd + [
            f"nohup {LLAMACPP_START_SCRIPT} '{gguf_path}' '{port}' '{n_gpu}' "
            f">/tmp/llamacpp.log 2>&1 &"
        ], timeout=5)
        time.sleep(4)
        return "started natively on host"

    try:
        msg = await loop.run_in_executor(None, _start)
        return {"status": "ok", "message": msg, "model": model, "gpu_layers": n_gpu}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/llamacpp/stop")
async def llamacpp_stop() -> dict[str, Any]:
    """Stop llama.cpp server on host."""
    loop = asyncio.get_event_loop()

    def _stop():
        _run_on_host(["pkill", "-f", "llama_cpp.server"], timeout=5)

    try:
        await loop.run_in_executor(None, _stop)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


async def _find_gguf_for_model(model_name: str) -> str | None:
    """Find GGUF blob for an Ollama model by checking manifests."""
    # Search possible Ollama model paths (env var, native, Docker, user)
    ollama_models = None
    search_paths = []
    env_dir = os.environ.get("OLLAMA_MODELS_DIR")
    if env_dir:
        search_paths.append(Path(env_dir))
    search_paths += [
        Path("/usr/share/ollama/.ollama/models"),   # native install (systemd user)
        Path(os.path.expanduser("~/.ollama/models")),  # current user
        Path("/root/.ollama/models"),                # Docker root
    ]
    for p in search_paths:
        if p.exists() and (p / "manifests").is_dir():
            ollama_models = p
            break
    if ollama_models is None:
        return None

    # Parse model name: "gemma3:1b" -> library/gemma3/1b
    parts = model_name.split(":")
    name = parts[0]
    tag = parts[1] if len(parts) > 1 else "latest"
    if "/" not in name:
        name = f"library/{name}"

    manifest_path = ollama_models / "manifests" / "registry.ollama.ai" / name / tag
    if not manifest_path.exists():
        return None

    try:
        manifest = json.loads(manifest_path.read_text())
        for layer in manifest.get("layers", []):
            if layer.get("mediaType", "") == "application/vnd.ollama.image.model":
                digest = layer["digest"].replace(":", "-")
                blob_path = ollama_models / "blobs" / digest
                if blob_path.exists():
                    return str(blob_path)
    except Exception:
        pass

    return None


class _OllamaPullState:
    def __init__(self) -> None:
        self.running = False
        self.model = ""
        self.status = ""
        self.total = 0
        self.completed = 0
        self.error: str | None = None
        self.done = False

    def to_dict(self) -> dict[str, Any]:
        pct = round(self.completed / self.total * 100, 1) if self.total > 0 else 0
        return {
            "running": self.running, "model": self.model,
            "status": self.status, "percent": pct,
            "total": self.total, "completed": self.completed,
            "error": self.error, "done": self.done,
        }


_pull_state = _OllamaPullState()


@router.post("/ollama/pull")
async def ollama_pull(req: OllamaModelRequest) -> dict[str, Any]:
    """Start pulling an Ollama model (async). Poll /ollama/pull-progress."""
    if _pull_state.running:
        return {"status": "already_running", **_pull_state.to_dict()}

    _pull_state.running = True
    _pull_state.model = req.model
    _pull_state.status = "starting"
    _pull_state.total = 0
    _pull_state.completed = 0
    _pull_state.error = None
    _pull_state.done = False

    asyncio.create_task(_ollama_pull_bg(req.model))
    return {"status": "started", "model": req.model}


@router.get("/ollama/pull-progress")
async def ollama_pull_progress() -> dict[str, Any]:
    """Poll Ollama model pull progress."""
    return _pull_state.to_dict()


async def _ollama_pull_bg(model: str) -> None:
    ollama_url = get_value("voice", "ollama_url", os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=30.0)) as client:
            async with client.stream(
                "POST", f"{ollama_url}/api/pull", json={"name": model}
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        _pull_state.status = data.get("status", "")
                        if "total" in data:
                            _pull_state.total = data["total"]
                        if "completed" in data:
                            _pull_state.completed = data["completed"]
                    except Exception:
                        pass
        _pull_state.done = True
        _pull_state.status = "success"
        logger.info("Ollama pull '%s' completed", model)
    except Exception as exc:
        logger.error("Ollama pull '%s' failed: %s", model, exc)
        _pull_state.error = str(exc)
        _pull_state.status = "error"
    finally:
        _pull_state.running = False


@router.post("/ollama/delete-model")
async def ollama_delete_model(req: OllamaModelRequest) -> dict[str, Any]:
    """Delete an Ollama model."""
    ollama_url = get_value("voice", "ollama_url", os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request("DELETE", f"{ollama_url}/api/delete", json={"name": req.model})
            resp.raise_for_status()
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ================================================================== #
#  STT Model Catalog (Whisper)                                         #
# ================================================================== #

@router.get("/stt/catalog")
async def stt_catalog() -> dict[str, Any]:
    """Return available Whisper model sizes."""
    return {
        "models": [
            {"id": "tiny",   "size_mb": 75,  "languages": 99, "description": "Fastest, lowest accuracy"},
            {"id": "base",   "size_mb": 142, "languages": 99, "description": "Good balance for Raspberry Pi"},
            {"id": "small",  "size_mb": 466, "languages": 99, "description": "Recommended for most hardware"},
            {"id": "medium", "size_mb": 1530, "languages": 99, "description": "High accuracy, needs 4GB+ RAM"},
        ],
        "provider": "whisper",
        "auto_lang_detect": True,
    }



# ================================================================== #
#  Piper Dynamic Voice Catalog                                         #
# ================================================================== #

@router.get("/tts/catalog")
async def tts_catalog() -> dict[str, Any]:
    """Fetch dynamic Piper voice catalog from HuggingFace. Cached 24h."""
    cache_file = CACHE_DIR / "piper_voices.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Check cache
    if cache_file.exists():
        import time
        age = time.time() - cache_file.stat().st_mtime
        if age < 86400:
            try:
                return json.loads(cache_file.read_text())
            except Exception:
                pass

    # Fetch from HuggingFace
    try:
        url = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.json()

        voices: list[dict[str, Any]] = []
        for key, v in raw.items():
            lang_info = v.get("language", {})
            files = v.get("files", {})
            total_size = sum(f.get("size_bytes", 0) for f in files.values())

            voices.append({
                "id": key,
                "name": v.get("name", key),
                "language": lang_info.get("family", ""),
                "language_code": lang_info.get("code", ""),
                "language_name": lang_info.get("name_english", ""),
                "country": lang_info.get("country_english", ""),
                "quality": v.get("quality", "medium"),
                "num_speakers": v.get("num_speakers", 1),
                "size_bytes": total_size,
            })

        voices.sort(key=lambda x: (x["language"], x["name"]))
        result = {"voices": voices, "source": "remote"}
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
        return result

    except Exception as exc:
        logger.warning("Piper catalog fetch failed: %s", exc)
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                data["source"] = "cache"
                return data
            except Exception:
                pass
        return {"voices": [], "source": "error", "error": str(exc)}


# ================================================================== #
#  Model / Voice Download & Delete                                     #
# ================================================================== #

_tts_download_state: dict[str, dict] = {}


@router.post("/tts/download")
async def tts_download(req: VoiceIdRequest) -> dict[str, Any]:
    """Download a Piper TTS voice."""
    voice_id = req.voice
    if voice_id in _tts_download_state and _tts_download_state[voice_id].get("running"):
        return {"status": "already_downloading"}

    _tts_download_state[voice_id] = {"running": True, "progress": "starting"}
    asyncio.create_task(_download_piper_voice(voice_id))
    return {"status": "started", "voice": voice_id}


async def _download_piper_voice(voice_id: str) -> None:
    """Download Piper voice .onnx + .onnx.json files."""
    PIPER_MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # Construct URL from voice ID pattern: {lang}/{locale}/{name}/{quality}/{id}.onnx
    # e.g., en_US-amy-medium -> en/en_US/amy/medium/en_US-amy-medium.onnx
    parts = voice_id.split("-", 1)  # ["en_US", "amy-medium"]
    if len(parts) < 2:
        _tts_download_state[voice_id] = {"running": False, "progress": "error", "error": t("api.invalid_voice_id")}
        return

    locale = parts[0]  # en_US
    lang = locale.split("_")[0]  # en
    rest = parts[1]  # amy-medium or amy-low
    # Split rest into name and quality
    rest_parts = rest.rsplit("-", 1)
    if len(rest_parts) == 2:
        name, quality = rest_parts
    else:
        name = rest
        quality = "medium"

    base_url = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
    files_to_download = [
        f"{base_url}/{lang}/{locale}/{name}/{quality}/{voice_id}.onnx",
        f"{base_url}/{lang}/{locale}/{name}/{quality}/{voice_id}.onnx.json",
    ]

    try:
        _tts_download_state[voice_id]["progress"] = "downloading"
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0), follow_redirects=True) as client:
            for url in files_to_download:
                filename = url.rsplit("/", 1)[-1]
                dest = PIPER_MODELS_DIR / filename
                if dest.exists():
                    continue
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(dest, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)

        _tts_download_state[voice_id] = {"running": False, "progress": "done", "success": True}
        logger.info("Piper voice %s downloaded", voice_id)
    except Exception as exc:
        logger.error("Piper voice download failed: %s", exc)
        _tts_download_state[voice_id] = {"running": False, "progress": "error", "success": False, "error": str(exc)}


@router.get("/tts/download-progress/{voice_id}")
async def tts_download_progress(voice_id: str) -> dict[str, Any]:
    """Check download progress for a Piper voice."""
    return _tts_download_state.get(voice_id, {"running": False, "progress": "idle"})


@router.post("/tts/delete")
async def tts_delete(req: VoiceIdRequest) -> dict[str, Any]:
    """Delete an installed Piper voice."""
    onnx = PIPER_MODELS_DIR / f"{req.voice}.onnx"
    json_file = PIPER_MODELS_DIR / f"{req.voice}.onnx.json"
    if onnx.exists():
        onnx.unlink()
        json_file.unlink(missing_ok=True)
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail=t("api.voice_not_found"))


# ================================================================== #
#  Cloud LLM Provider Management                                       #
# ================================================================== #

@router.get("/llm/providers")
async def llm_providers() -> dict[str, Any]:
    """List all supported LLM providers with config status."""
    from system_modules.llm_engine.cloud_providers import get_provider_list
    providers = get_provider_list()
    config = read_config()
    voice_cfg = config.get("voice", {})
    provider_configs = voice_cfg.get("providers", {})
    active = voice_cfg.get("llm_provider", "ollama")

    result = []
    for p in providers:
        pid = p["id"]
        p_cfg = provider_configs.get(pid, {})
        has_key = bool(p_cfg.get("api_key"))
        result.append({
            **p,
            "configured": has_key or pid == "ollama",
            "active": pid == active,
            "model": p_cfg.get("model", ""),
        })

    return {"providers": result, "active": active}


@router.post("/llm/provider/select")
async def llm_provider_select(req: ProviderSelectRequest) -> dict[str, Any]:
    """Switch active LLM provider. Auto-manages local servers:
    - Ollama selected → start Ollama, stop llama.cpp
    - llama.cpp selected → start llama.cpp, stop Ollama
    - Cloud selected → stop both Ollama and llama.cpp
    """
    config = read_config()
    voice_cfg = config.setdefault("voice", {})
    provider_configs = voice_cfg.get("providers", {})
    old_provider = voice_cfg.get("llm_provider", "ollama")

    voice_cfg["llm_provider"] = req.provider

    # Update llm_model to this provider's saved model
    p_cfg = provider_configs.get(req.provider, {})
    saved_model = p_cfg.get("model", "")
    if saved_model:
        voice_cfg["llm_model"] = saved_model

    from core.config_writer import write_config
    write_config(config)

    # Auto-manage local servers in background (don't block the response)
    asyncio.create_task(_switch_local_servers(req.provider, saved_model))

    return {"status": "ok", "provider": req.provider, "model": saved_model}


async def _switch_local_servers(provider: str, model: str) -> None:
    """Background task: stop/start local LLM servers based on selected provider."""
    loop = asyncio.get_event_loop()

    def _force_kill_ollama() -> None:
        """Kill ollama serve regardless of how it was started."""
        try:
            _run_on_host(["pkill", "-f", "ollama serve"], timeout=5)
        except Exception:
            pass

    def _force_kill_llamacpp() -> None:
        """Kill llama_cpp.server regardless of how it was started."""
        try:
            _run_on_host(["pkill", "-f", "llama_cpp.server"], timeout=5)
        except Exception:
            pass

    try:
        if provider == "ollama":
            try: await llamacpp_stop()
            except Exception: pass
            await loop.run_in_executor(None, _force_kill_llamacpp)
            await asyncio.sleep(2)
            try: await ollama_start()
            except Exception: pass

        elif provider == "llamacpp":
            try: await ollama_stop()
            except Exception: pass
            await loop.run_in_executor(None, _force_kill_ollama)
            await asyncio.sleep(2)
            if model:
                try: await llamacpp_start({"model": model})
                except Exception: pass

        else:
            # Cloud provider — stop both local servers to free GPU RAM
            try: await ollama_stop()
            except Exception: pass
            try: await llamacpp_stop()
            except Exception: pass
            await loop.run_in_executor(None, _force_kill_ollama)
            await loop.run_in_executor(None, _force_kill_llamacpp)
    except Exception as exc:
        logger.warning("Server switch failed: %s", exc)


@router.post("/llm/provider/apikey")
async def llm_provider_apikey(req: ApiKeyRequest) -> dict[str, Any]:
    """Save API key for a cloud provider."""
    config = read_config()
    voice_cfg = config.setdefault("voice", {})
    providers = voice_cfg.setdefault("providers", {})
    p_cfg = providers.setdefault(req.provider, {})
    p_cfg["api_key"] = req.api_key

    from core.config_writer import write_config
    write_config(config)

    return {"status": "ok"}


@router.post("/llm/provider/validate")
async def llm_provider_validate(req: ApiKeyRequest) -> dict[str, Any]:
    """Validate API key and return available models on success."""
    from system_modules.llm_engine.cloud_providers import validate_api_key, list_models
    result = await validate_api_key(req.provider, req.api_key)
    if result["valid"]:
        models = await list_models(req.provider, req.api_key)
        result["models"] = models
    return result


@router.get("/llm/provider/models")
async def llm_provider_models(provider: str | None = None) -> dict[str, Any]:
    """Get models for a provider. Uses active provider if not specified."""
    config = read_config()
    voice_cfg = config.get("voice", {})
    active = provider or voice_cfg.get("llm_provider", "ollama")

    if active == "ollama":
        # Delegate to ollama_models endpoint logic
        data = await ollama_models()
        return data

    provider_configs = voice_cfg.get("providers", {})
    p_cfg = provider_configs.get(active, {})
    api_key = p_cfg.get("api_key", "")

    if not api_key:
        return {"models": [], "error": t("api.no_api_key_configured")}

    from system_modules.llm_engine.cloud_providers import list_models
    models = await list_models(active, api_key)
    return {"models": models}


@router.post("/llm/provider/model")
async def llm_provider_model_select(req: ProviderModelRequest) -> dict[str, Any]:
    """Save selected model for a provider."""
    config = read_config()
    voice_cfg = config.setdefault("voice", {})
    providers = voice_cfg.setdefault("providers", {})
    p_cfg = providers.setdefault(req.provider, {})
    p_cfg["model"] = req.model

    # If this is the active provider, also update llm_model
    if voice_cfg.get("llm_provider") == req.provider:
        voice_cfg["llm_model"] = req.model

    from core.config_writer import write_config
    write_config(config)

    return {"status": "ok"}


# ================================================================== #
#  Shared utilities                                                    #
# ================================================================== #

async def _pip_action(state: _InstallState, action: str, package: str) -> None:
    """Run pip install or uninstall in background."""
    loop = asyncio.get_event_loop()

    def _run():
        packages = package.split()
        if action == "install":
            cmd = ["pip", "install"] + packages
        else:
            cmd = ["pip", "uninstall", "-y"] + packages
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return result

    try:
        result = await loop.run_in_executor(None, _run)
        state.output = result.stdout + result.stderr
        state.success = result.returncode == 0
    except Exception as exc:
        state.output = str(exc)
        state.success = False
    finally:
        state.running = False


async def _shell_action(state: _InstallState, cmd: list[str], timeout: int = 120) -> None:
    """Run shell command in background."""
    loop = asyncio.get_event_loop()

    def _run():
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result

    try:
        result = await loop.run_in_executor(None, _run)
        state.output = result.stdout + result.stderr
        state.success = result.returncode == 0
    except Exception as exc:
        state.output = str(exc)
        state.success = False
    finally:
        state.running = False
