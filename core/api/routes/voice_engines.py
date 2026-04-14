"""
core/api/routes/voice_engines.py — Voice engine management API.

Endpoints for:
  - Vosk STT test endpoint
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
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from core.config_writer import get_value, read_config, update_config, update_many

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
    """Record audio from mic, run Vosk STT, return recognized text + audio as base64."""
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

    # Pause voice loop to release microphone
    try:
        from core.module_loader.sandbox import get_sandbox
        vc = get_sandbox().get_in_process_module("voice-core")
        if vc and hasattr(vc, "_mic_test_active"):
            vc._mic_test_active = True
            proc_arecord = getattr(vc, "_arecord_proc", None)
            if proc_arecord and proc_arecord.poll() is None:
                proc_arecord.kill()
                proc_arecord.wait(timeout=2)
            import time; time.sleep(0.3)
    except Exception:
        pass

    try:
        raw = await asyncio.wait_for(loop.run_in_executor(None, _record), timeout=duration + 5)
        if len(raw) < 100:
            return {"status": "error", "text": "", "error": "No audio recorded"}

        # Peak level
        samples = struct.unpack(f"<{len(raw) // 2}h", raw)
        peak = max(abs(s) for s in samples) / 32768.0 if samples else 0

        # STT via provider abstraction (Vosk)
        try:
            from core.stt import create_stt_provider
            provider = create_stt_provider()
            result = await provider.transcribe(raw, sample_rate=16000)
            text = result.text
            lang = result.lang
        except Exception as exc:
            return {"status": "error", "text": "", "error": f"STT error: {exc}"}

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
    finally:
        # Resume voice loop
        try:
            from core.module_loader.sandbox import get_sandbox
            vc = get_sandbox().get_in_process_module("voice-core")
            if vc and hasattr(vc, "_mic_test_active"):
                vc._mic_test_active = False
        except Exception:
            pass


@router.post("/tts/speak")
async def tts_speak(req: SttTtsTestRequest) -> Any:
    """Synthesize text via Piper TTS and return WAV audio."""
    from fastapi.responses import Response

    if not req.text.strip():
        raise HTTPException(status_code=422, detail="Text is empty")

    voice = req.voice or get_value("voice", "tts_voice", os.environ.get("PIPER_VOICE", "uk_UA-ukrainian_tts-medium"))

    try:
        from system_modules.voice_core.tts import get_tts_engine
        engine = get_tts_engine()
        wav_bytes = await engine.synthesize(req.text[:500], voice=voice)
        if not wav_bytes:
            raise HTTPException(status_code=503, detail="TTS synthesis failed")

        # Also play on device if requested
        if req.output_device and req.output_device != "none":
            asyncio.create_task(_play_wav_on_device(wav_bytes, req.output_device))

        return Response(content=wav_bytes, media_type="audio/wav")
    except ImportError:
        raise HTTPException(status_code=503, detail="Piper TTS not installed")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("TTS speak failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


MAX_PROMPT_LEN = 5000  # universal max for any prompt
MIN_SYSTEM_PROMPT_LEN = 100  # the user-editable 'system' prompt must keep at
                             # least this much identity/behaviour text, so the
                             # dynamic keyword-filtered catalog still has a
                             # coherent shell to slot into.

from core.lang_utils import lang_code_to_name


def _detect_lang_from_stt(stt_model: str) -> str:
    """Detect language from STT config."""
    try:
        from core.config_writer import read_config
        lang = read_config().get("voice", {}).get("tts", {}).get("primary", {}).get("lang", "en")
        lang_names = {"en": "English", "uk": "Ukrainian", "ru": "Russian"}
        return lang_names.get(lang, lang)
    except Exception:
        return "English"


def _extract_name_from_wake(wake_phrase: str) -> str:
    """Extract the last word of a wake phrase as the assistant name.

    The result is transliterated to Latin if it contains Cyrillic, so
    ``{name}`` in the English-only prompt is always pronounceable by
    Piper voices in any language.
    """
    parts = wake_phrase.replace("_", " ").strip().split()
    native = ""
    if len(parts) >= 2:
        native = parts[-1]
    elif len(parts) == 1:
        native = parts[0]
    if not native:
        return "Selena"
    if any("\u0400" <= ch <= "\u04ff" for ch in native):
        from core.translit import cyrillic_to_latin
        return (cyrillic_to_latin(native).capitalize() or "Selena")
    return native.capitalize()


def _get_prompt_context() -> tuple[str, str, str]:
    """Return (assistant_name_en, response_language, tts_lang_code).

    Name is the English form of the wake word (``voice.wake_word_en``),
    falling back to a transliteration of ``voice.wake_word_model``. The
    prompt is English-only and OutputTranslator handles the TTS-language
    conversion downstream, so we always return the English name here.
    """
    config = read_config()
    voice_cfg = config.get("voice", {})
    sys_cfg = config.get("system", {})

    wake_en = (voice_cfg.get("wake_word_en") or "").strip()
    if wake_en:
        name = wake_en.split()[-1].capitalize()
    else:
        wake = voice_cfg.get("wake_word_model", "")
        name = _extract_name_from_wake(wake) if wake else "Selena"

    tts_lang = voice_cfg.get("tts", {}).get("primary", {}).get("lang", "")
    if not tts_lang:
        tts_lang = sys_cfg.get("language", "en")
    lang = lang_code_to_name(tts_lang)
    return name, lang, tts_lang


def _flush_llm_caches() -> None:
    """Refresh router state after a prompt or language change.

    Historically this also purged ``IntentCache``, but the cache was
    removed because it could return a stale classification pointing at a
    deleted or renamed entity. ``refresh_system_prompt()`` is a no-op
    these days — the catalog is built per-request — but we keep the call
    as a hook for future router state.
    """
    try:
        from system_modules.llm_engine.intent_router import get_intent_router
        get_intent_router().refresh_system_prompt()
    except Exception as exc:
        logger.warning("Failed to refresh IntentRouter: %s", exc)


async def _build_prompt_preview(compact: bool = False) -> str:
    """Build a prompt preview for the UI using core.llm prompt resolution.

    Args:
        compact: If True, preview the compact (local model) prompt.
    """
    from core.llm import _resolve_system_prompt, _get_provider
    provider, _ = _get_provider()
    if compact:
        # Force local provider to get compact variant
        return await _resolve_system_prompt("chat", "ollama")
    return await _resolve_system_prompt("chat", provider)


@router.get("/llm/system-prompt")
async def get_system_prompt() -> dict[str, Any]:
    """Get the unified system prompt from DB (English slot only).

    Core operates in English end-to-end — the single ``system`` prompt
    lives in the ``en`` row; OutputTranslator converts responses to the
    TTS language downstream. There is no per-language prompt anymore.
    """
    from core.prompt_store import get_prompt_store, PROMPT_KEYS, _EN_FALLBACK
    store = get_prompt_store()
    name, lang, tts_lang = _get_prompt_context()

    prompts_meta: dict[str, dict] = {}
    for key in PROMPT_KEYS:
        meta = await store.get_meta("en", key)
        prompts_meta[key] = {
            "value": meta["value"],
            "is_custom": meta["is_custom"],
            "default": _EN_FALLBACK.get(key, ""),
        }

    return {
        "name": name,
        "lang": lang,
        "ui_lang": tts_lang,
        "prompts": prompts_meta,
        "limits": {
            "max_prompt_len": MAX_PROMPT_LEN,
            "min_system_prompt_len": MIN_SYSTEM_PROMPT_LEN,
        },
        "full_preview": await _build_prompt_preview(compact=False),
        "compact_preview": await _build_prompt_preview(compact=True),
    }


@router.post("/llm/prompt")
async def save_any_prompt(body: dict[str, Any]) -> dict[str, Any]:
    """Save a prompt by key into the English slot."""
    from core.prompt_store import get_prompt_store, PROMPT_KEYS
    key = body.get("key", "")
    value = body.get("value", "").strip()
    if key not in PROMPT_KEYS:
        raise HTTPException(400, f"Unknown prompt key: {key}")
    if len(value) > MAX_PROMPT_LEN:
        raise HTTPException(
            400, f"Prompt exceeds max length ({len(value)} > {MAX_PROMPT_LEN})",
        )
    # The editable 'system' prompt is the identity/behaviour shell the
    # dynamic catalog gets appended to. Without a minimum we end up with
    # an empty system prompt and the LLM free-form hallucinates.
    if key == "system" and len(value) < MIN_SYSTEM_PROMPT_LEN:
        raise HTTPException(
            400,
            f"System prompt too short ({len(value)} < {MIN_SYSTEM_PROMPT_LEN} chars). "
            f"Keep the core identity + rules block.",
        )
    await get_prompt_store().set("en", key, value, is_custom=True)
    _flush_llm_caches()
    return {"status": "ok", "key": key}


@router.post("/llm/system-prompt/reset")
async def reset_system_prompt() -> dict[str, Any]:
    """Reset all prompts to the English defaults from en.json."""
    from core.prompt_store import get_prompt_store
    store = get_prompt_store()
    await store.reset("en")
    prompts = await store.get_all("en")
    _flush_llm_caches()
    return {"status": "ok", "prompts": prompts}


@router.post("/llm/rebuild")
async def rebuild_prompts() -> dict[str, Any]:
    """Rebuild prompts after any settings change (language, name, STT model, etc.).

    Reloads prompt cache from DB. Call after language or wake word change.
    """
    _flush_llm_caches()

    name, lang, tts_lang = _get_prompt_context()
    return {
        "status": "ok",
        "name": name,
        "lang": lang,
        "ui_lang": tts_lang,
        "full_preview": await _build_prompt_preview(compact=False),
        "compact_preview": await _build_prompt_preview(compact=True),
    }


async def _translate_prompts_on_lang_change(old_lang: str, new_lang: str) -> None:
    """Handle TTS language change: translate custom prompts, generate defaults.

    1. Custom (user-edited) prompts → translated via LLM to new language
    2. Default prompts → generated from English via LLM for new language
    """
    from core.prompt_store import get_prompt_store
    store = get_prompt_store()

    # First: translate any custom prompts
    await store.translate_custom_prompts(old_lang, new_lang)

    # Then: ensure default prompts exist for new language
    # (generate from English if not already in DB)
    prompts = await store.get_all(new_lang)
    has_prompts = any(v for v in prompts.values())
    if not has_prompts:
        await store.generate_for_language(new_lang)

    logger.info("Prompts updated for TTS language change: %s → %s", old_lang, new_lang)


@router.post("/llm/chat")
async def llm_chat(req: LlmChatRequest) -> dict[str, Any]:
    """Send text to active LLM provider and return response.

    Uses core.llm.llm_call() for standard requests.
    When req.system is provided (UI test/preview feature), calls the
    provider directly with the custom system prompt.
    """
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="Text is empty")

    from core.llm import llm_call, _get_provider, _call_provider
    from core.config_writer import get_value as _cfg_get

    provider, provider_cfg = _get_provider()
    is_local = provider == "ollama"
    _, response_lang, _ = _get_prompt_context()

    # Per docs/translation.md: core operates in English. Translate any
    # non-English input to English before sending to the LLM, then
    # translate the English reply back. Without this the LLM mirrors
    # the user's language and the en→target Argos pass on already-Cyrillic
    # output produces garbage.
    text_in = req.text
    if _cfg_get("translation", "enabled", False):
        from core.translation.local_translator import get_input_translator
        from system_modules.voice_core.module import _resolve_active_lang
        in_lang = _resolve_active_lang()
        if in_lang != "en":
            inp = get_input_translator()
            if inp.is_available():
                text_in = inp.to_english(text_in, in_lang) or text_in

    # Language tag for local models — reinforces system prompt for small models
    user_msg = f"[{response_lang}] {text_in}" if is_local else text_in

    # Resolve model name for response metadata
    config = read_config()
    voice_cfg = config.get("voice", {})
    if provider == "ollama":
        model = voice_cfg.get("providers", {}).get("ollama", {}).get("model", "") or \
                voice_cfg.get("llm_model", "") or os.environ.get("OLLAMA_MODEL", "")
    else:
        model = voice_cfg.get("providers", {}).get(provider, {}).get("model", "")

    try:
        if req.system:
            # UI test/preview with custom system prompt — bypass llm_call prompt loading
            response_text = await _call_provider(
                provider, provider_cfg, req.system, user_msg, 0.7, 512,
            )
        else:
            response_text = await llm_call(
                user_msg, prompt_key="chat", temperature=0.7, timeout=120.0,
            )

        if not response_text:
            return {"status": "error", "response": "", "error": "LLM returned empty response", "provider": provider}

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
        raise HTTPException(status_code=422, detail="Text is empty")

    voice = req.voice or get_value("voice", "tts_voice", os.environ.get("PIPER_VOICE", "uk_UA-ukrainian_tts-medium"))
    device = req.output_device or "default"
    # Resolve "default" to the configured output device
    if device == "default":
        device = get_value("voice", "audio_force_output", "") or "default"
    clean = sanitize_for_tts(req.text[:500])
    if not clean:
        raise HTTPException(status_code=422, detail="empty after sanitize")

    # Single-voice setup: always use primary settings.
    config = read_config()
    tts_cfg = config.get("voice", {}).get("tts", {})
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

    if not pcm_data:
        raise HTTPException(
            status_code=503,
            detail=f"Piper TTS server unreachable at {gpu_url}",
        )
    size_kb = round(len(pcm_data) / 1024, 1)
    sample_rate = 22050
    t1 = _time.monotonic()
    await loop.run_in_executor(None, _aplay_raw_pcm, pcm_data, device, sample_rate)
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
async def tts_status() -> dict[str, Any]:
    """Get single-voice TTS status (primary only).

    Endpoint path keeps the legacy ``dual-status`` name for UI
    compatibility; the response carries only ``primary`` now — no
    fallback voice is loaded or saved anymore.
    """
    config = read_config()
    tts_cfg = config.get("voice", {}).get("tts", {})

    if not tts_cfg:
        tts_cfg = {
            "primary": {
                "voice": config.get("voice", {}).get("tts_voice", "uk_UA-ukrainian_tts-medium"),
                "lang": "uk",
                "cuda": False,
                "settings": config.get("voice", {}).get("tts_settings", TTS_DEFAULTS),
            },
        }

    role_cfg = tts_cfg.get("primary", {})
    voice_id = role_cfg.get("voice", "")
    lang = role_cfg.get("lang", "uk")
    cuda = role_cfg.get("cuda", False)
    settings = {**TTS_DEFAULTS, **role_cfg.get("settings", {})}

    model_exists = (PIPER_MODELS_DIR / f"{voice_id}.onnx").exists() if voice_id else False
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

    return {
        "primary": {
            "voice": voice_id,
            "lang": lang,
            "cuda": cuda,
            "installed": model_exists,
            "num_speakers": num_speakers,
            "sample_rate": sample_rate,
            "settings": settings,
        },
    }


@router.post("/tts/dual-config")
async def tts_config_save(req: dict[str, Any]) -> dict[str, Any]:
    """Save single-voice TTS config (primary only).

    Endpoint path keeps the legacy ``dual-config`` name for UI
    compatibility; any ``fallback`` key in the request body is ignored.
    """
    primary = req.get("primary") or {}
    role_cfg: dict[str, Any] = {}
    if "voice" in primary:
        role_cfg["voice"] = str(primary["voice"])
    if "lang" in primary:
        role_cfg["lang"] = str(primary["lang"])
    if "cuda" in primary:
        role_cfg["cuda"] = bool(primary["cuda"])
    if "settings" in primary:
        s = primary["settings"]
        role_cfg["settings"] = {
            "length_scale": round(max(0.1, min(3.0, float(s.get("length_scale", 1.0)))), 2),
            "noise_scale": round(max(0.0, min(1.0, float(s.get("noise_scale", 0.667)))), 3),
            "noise_w_scale": round(max(0.0, min(1.0, float(s.get("noise_w_scale", 0.8)))), 3),
            "volume": round(max(0.1, min(3.0, float(s.get("volume", 1.0)))), 2),
            "speaker": int(s.get("speaker", 0)),
        }

    old_cfg = read_config().get("voice", {}).get("tts", {})
    old_lang = old_cfg.get("primary", {}).get("lang", "")
    new_lang = role_cfg.get("lang", old_lang)

    update_config("voice", "tts", {"primary": role_cfg})

    if new_lang and old_lang and new_lang != old_lang:
        logger.info("TTS language changed: %s → %s, flushing caches", old_lang, new_lang)
        _flush_llm_caches()

    return {"status": "ok"}


@router.post("/tts/upload")
async def tts_upload(
    model: UploadFile = File(...),
    config: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload a Piper voice pair (.onnx + .onnx.json) to voice.tts.models_dir."""
    import json as _json
    import os as _os

    MAX_MODEL = 500 * 1024 * 1024
    MAX_CONFIG = 1 * 1024 * 1024

    m_name = _os.path.basename(model.filename or "")
    c_name = _os.path.basename(config.filename or "")

    if not m_name.endswith(".onnx"):
        raise HTTPException(400, "model file must end in .onnx")
    if not c_name.endswith(".onnx.json"):
        raise HTTPException(400, "config file must end in .onnx.json")
    voice_id = m_name[:-len(".onnx")]
    config_stem = c_name[:-len(".onnx.json")]
    if voice_id != config_stem or not voice_id:
        raise HTTPException(400, "model/config stems must match")

    models_dir = PIPER_MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)
    models_real = _os.path.realpath(models_dir)
    onnx_path = models_dir / f"{voice_id}.onnx"
    json_path = models_dir / f"{voice_id}.onnx.json"
    for p in (onnx_path, json_path):
        if not _os.path.realpath(p).startswith(models_real + _os.sep):
            raise HTTPException(400, "invalid path")
    if onnx_path.exists():
        raise HTTPException(409, f"voice '{voice_id}' already exists — delete it first")

    model_bytes = await model.read()
    config_bytes = await config.read()
    if len(model_bytes) > MAX_MODEL:
        raise HTTPException(413, "model file too large (max 500 MB)")
    if len(config_bytes) > MAX_CONFIG:
        raise HTTPException(413, "config file too large (max 1 MB)")
    try:
        cfg_json = _json.loads(config_bytes.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(400, f"config json invalid: {exc}")

    onnx_path.write_bytes(model_bytes)
    json_path.write_bytes(config_bytes)

    language = ""
    try:
        language = (cfg_json.get("language") or {}).get("code") or ""
        if not language:
            language = cfg_json.get("espeak", {}).get("voice", "") or ""
    except Exception:
        pass

    return {"status": "ok", "voice": voice_id, "language": language}


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
#  Vosk STT Status (compat endpoint for voice_engines)                 #
#  Full Vosk management API is in core/api/routes/vosk.py              #
# ================================================================== #


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
    """Check if Ollama is installed and running.

    Ollama may run natively on the host while Selena runs in a container — in
    that case ``shutil.which("ollama")`` returns ``None`` because the host
    binary is not on the container's PATH. The HTTP API is the authoritative
    source of truth: if it answers, Ollama is both installed and running.
    """
    ollama_url = get_value("voice", "ollama_url", os.environ.get("OLLAMA_URL", "http://localhost:11434"))

    running = False
    version = None
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            running = resp.status_code == 200
            if running:
                try:
                    ver_resp = await client.get(f"{ollama_url}/api/version")
                    if ver_resp.status_code == 200:
                        version = (ver_resp.json().get("version") or "").strip() or None
                except Exception:
                    pass
    except Exception:
        running = False

    # Local binary fallback for the rare case where the API is down but the
    # binary is present (e.g. service stopped on the host).
    ollama_bin = shutil.which("ollama")
    if not running and ollama_bin and version is None:
        try:
            result = subprocess.run(
                ["ollama", "--version"], capture_output=True, text=True, timeout=5
            )
            version = result.stdout.strip().replace("ollama version ", "") or "unknown"
        except Exception:
            version = "unknown"

    installed = running or (ollama_bin is not None)

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


def _run_on_host(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Run command on host via nsenter (PID 1 namespace)."""
    return subprocess.run(
        ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--"] + cmd,
        capture_output=True, text=True, timeout=timeout,
    )


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
#  STT Model Catalog — moved to /api/ui/vosk/catalog                   #
# ================================================================== #



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
        _tts_download_state[voice_id] = {"running": False, "progress": "error", "error": "Invalid voice ID"}
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
    raise HTTPException(status_code=404, detail="Voice not found")


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
    """Switch active LLM provider. Auto-manages the local Ollama server:
    - Ollama selected → start Ollama
    - Cloud selected → stop Ollama to free GPU RAM
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
    """Background task: stop/start the local Ollama server based on selected provider."""
    loop = asyncio.get_event_loop()

    def _force_kill_ollama() -> None:
        """Kill ollama serve regardless of how it was started."""
        try:
            _run_on_host(["pkill", "-f", "ollama serve"], timeout=5)
        except Exception:
            pass

    try:
        if provider == "ollama":
            try: await ollama_start()
            except Exception: pass
        else:
            # Cloud provider — stop Ollama to free GPU RAM
            try: await ollama_stop()
            except Exception: pass
            await loop.run_in_executor(None, _force_kill_ollama)
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
        return {"models": [], "error": "No API key configured"}

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
