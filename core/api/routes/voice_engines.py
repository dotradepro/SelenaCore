"""
core/api/routes/voice_engines.py — Voice engine management API.

Endpoints for:
  - Vosk binary install/uninstall/status + dynamic model catalog
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
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config_writer import get_value, read_config, update_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/setup", tags=["voice-engines"])

CACHE_DIR = Path(os.environ.get("SELENA_CACHE_DIR", "/var/lib/selena/cache"))
VOSK_MODELS_DIR = Path(os.environ.get("VOSK_MODELS_DIR", "/var/lib/selena/models/vosk"))
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

    try:
        raw = await asyncio.wait_for(loop.run_in_executor(None, _record), timeout=duration + 5)
        if len(raw) < 100:
            return {"status": "error", "text": "", "error": "No audio recorded"}

        # Peak level
        samples = struct.unpack(f"<{len(raw) // 2}h", raw)
        peak = max(abs(s) for s in samples) / 32768.0 if samples else 0

        # STT
        active_model = get_value("voice", "stt_model", os.environ.get("VOSK_MODEL", "vosk-model-small-uk-v3-small"))
        try:
            from system_modules.voice_core.stt import STTEngine
            engine = STTEngine(model=active_model)
            text = await engine.transcribe(raw, sample_rate=16000)
        except ImportError:
            return {"status": "error", "text": "", "error": "Vosk not installed"}
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
        raise HTTPException(status_code=422, detail="Text is empty")

    voice = req.voice or get_value("voice", "tts_voice", os.environ.get("PIPER_VOICE", "uk_UA-ukrainian_tts-medium"))

    try:
        from system_modules.voice_core.tts import TTSEngine
        engine = TTSEngine(voice=voice)
        wav_bytes = await engine.synthesize(req.text[:500])
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


DEFAULT_SYSTEM_PROMPT = (
    "Ти голосовий ассистент розумного дому. Твоє ім'я Селена. "
    "Ти ЗАВЖДИ відповідаєш тільки як Селена. "
    "Ти НІКОЛИ не кажеш що ти мовна модель, нейромережа, AI або штучний інтелект. "
    "Ти НІКОЛИ не називаєш Google, OpenAI, Meta або будь-якого розробника. "
    "Якщо питають хто ти — відповідай: Я Селена, ваш домашній ассистент. "
    "Якщо питають хто тебе створив — відповідай: Мене створила команда SelenaCore. "
    "Відповідай коротко, мовою користувача."
)


@router.get("/llm/system-prompt")
async def get_system_prompt() -> dict[str, Any]:
    """Get the current system prompt for LLM."""
    config = read_config()
    saved = config.get("voice", {}).get("system_prompt", "")
    return {
        "prompt": saved or DEFAULT_SYSTEM_PROMPT,
        "is_custom": bool(saved),
        "default": DEFAULT_SYSTEM_PROMPT,
    }


@router.post("/llm/system-prompt")
async def save_system_prompt(req: SystemPromptRequest) -> dict[str, Any]:
    """Save custom system prompt for LLM."""
    prompt = req.prompt.strip()
    if prompt == DEFAULT_SYSTEM_PROMPT:
        prompt = ""  # don't save if it's the default
    update_config("voice", "system_prompt", prompt)
    return {"status": "ok"}


@router.post("/llm/system-prompt/reset")
async def reset_system_prompt() -> dict[str, Any]:
    """Reset system prompt to default."""
    update_config("voice", "system_prompt", "")
    return {"status": "ok", "prompt": DEFAULT_SYSTEM_PROMPT}


@router.post("/llm/chat")
async def llm_chat(req: LlmChatRequest) -> dict[str, Any]:
    """Send text to active LLM provider and return response."""
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="Text is empty")

    config = read_config()
    voice_cfg = config.get("voice", {})
    provider = voice_cfg.get("llm_provider", "ollama")

    # Use saved system prompt, fall back to default, allow override from request
    saved_prompt = voice_cfg.get("system_prompt", "")
    system_prompt = req.system or saved_prompt or DEFAULT_SYSTEM_PROMPT

    # Always append TTS formatting rules
    tts_rules = (
        "\nIMPORTANT: Your response will be read aloud by a TTS engine. "
        "Do NOT use markdown, code blocks, bullet points, asterisks, URLs, emojis, or any special formatting. "
        "Write plain natural text only."
    )
    if "TTS" not in system_prompt:
        system_prompt += tts_rules

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
                        return {"status": "error", "response": "", "error": "Ollama server not running", "provider": provider}
            except Exception:
                return {"status": "error", "response": "", "error": "Ollama server not available", "provider": provider}

            # Use /api/chat (messages format) — models follow system prompt much better
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": req.text})

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
                    resp = await client.get(f"{llamacpp_url}/health")
                    if resp.status_code != 200:
                        return {"status": "error", "response": "", "error": "llama.cpp server not running", "provider": provider}
            except Exception:
                return {"status": "error", "response": "", "error": "llama.cpp server not available", "provider": provider}

            # OpenAI-compatible API
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": req.text})

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
                return {"status": "error", "response": "", "error": f"No API key for {provider}", "provider": provider}
            if not model:
                return {"status": "error", "response": "", "error": f"No model selected for {provider}", "provider": provider}

            from system_modules.llm_engine.cloud_providers import generate
            response_text = await generate(provider, api_key, model, req.text, system_prompt)

            if not response_text:
                return {"status": "error", "response": "", "error": "LLM returned empty response", "provider": provider}

            return {"status": "ok", "response": response_text.lower(), "provider": provider, "model": model}

    except Exception as exc:
        logger.error("LLM chat failed: %s", exc)
        return {"status": "error", "response": "", "error": str(exc), "provider": provider}


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


_vosk_install = _InstallState()
_piper_install = _InstallState()
_ollama_install = _InstallState()


# ================================================================== #
#  Vosk Binary Management                                              #
# ================================================================== #

@router.get("/vosk/status")
async def vosk_status() -> dict[str, Any]:
    """Check if Vosk is installed."""
    try:
        import vosk  # noqa: F401
        version = getattr(vosk, "__version__", None)
        if not version:
            try:
                result = subprocess.run(["pip", "show", "vosk"], capture_output=True, text=True, timeout=10)
                for line in result.stdout.splitlines():
                    if line.startswith("Version:"):
                        version = line.split(":", 1)[1].strip()
                        break
            except Exception:
                pass
        return {"installed": True, "version": version or "installed"}
    except ImportError:
        return {"installed": False, "version": None}


@router.post("/vosk/install")
async def vosk_install() -> dict[str, Any]:
    """Install Vosk via pip."""
    if _vosk_install.running:
        return {"status": "already_running", **_vosk_install.to_dict()}
    _vosk_install.reset()
    _vosk_install.running = True
    _vosk_install.package = "vosk"
    _vosk_install.action = "install"
    asyncio.create_task(_pip_action(_vosk_install, "install", "vosk"))
    return {"status": "started"}


@router.post("/vosk/uninstall")
async def vosk_uninstall() -> dict[str, Any]:
    """Uninstall Vosk via pip."""
    if _vosk_install.running:
        return {"status": "already_running", **_vosk_install.to_dict()}
    _vosk_install.reset()
    _vosk_install.running = True
    _vosk_install.package = "vosk"
    _vosk_install.action = "uninstall"
    asyncio.create_task(_pip_action(_vosk_install, "uninstall", "vosk"))
    return {"status": "started"}


@router.get("/vosk/install-progress")
async def vosk_install_progress() -> dict[str, Any]:
    """Poll Vosk install/uninstall progress."""
    return _vosk_install.to_dict()


# ================================================================== #
#  Piper Binary Management                                             #
# ================================================================== #

@router.get("/piper/status")
async def piper_status() -> dict[str, Any]:
    """Check if Piper TTS is installed."""
    piper_bin = shutil.which("piper")
    version = None

    # Get version from pip
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
    """Install Piper TTS via pip."""
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
    """Start Ollama server (Docker container or local process)."""
    loop = asyncio.get_event_loop()

    def _start():
        # Try starting Docker container first (GPU setup)
        if shutil.which("docker"):
            result = subprocess.run(
                ["docker", "start", "selena-ollama"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                return "started docker container"

        # Try systemctl (host systems)
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
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        import time
        time.sleep(2)  # give it a moment to bind
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
        # Try stopping Docker container first (GPU setup)
        if shutil.which("docker"):
            result = subprocess.run(
                ["docker", "stop", "selena-ollama"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                return "stopped docker container"

        # Fallback: systemctl
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
    """List models from Ollama (installed + available to pull)."""
    ollama_url = get_value("voice", "ollama_url", os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    installed: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
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
    except Exception as exc:
        logger.warning("Ollama model list failed: %s", exc)

    return {"models": installed}


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
            resp = await client.get(f"{llamacpp_url}/health")
            running = resp.status_code == 200
    except Exception:
        pass
    return {"running": running, "url": llamacpp_url}


@router.post("/llamacpp/start")
async def llamacpp_start(body: dict[str, Any] = {}) -> dict[str, Any]:
    """Start llama.cpp server with a model."""
    model = body.get("model", "")
    if not model:
        # Use active model from config
        config = read_config()
        voice_cfg = config.get("voice", {})
        p_cfg = voice_cfg.get("providers", {}).get("llamacpp", {})
        model = p_cfg.get("model", "")

    if not model:
        raise HTTPException(status_code=422, detail="No model specified")

    # Find GGUF file — check ollama blobs for this model
    gguf_path = await _find_gguf_for_model(model)
    if not gguf_path:
        raise HTTPException(status_code=404, detail=f"GGUF file not found for {model}. Pull model via Ollama first.")

    llamacpp_url = get_value("voice", "llamacpp_url", "http://localhost:8081")
    port = llamacpp_url.rsplit(":", 1)[-1] if ":" in llamacpp_url else "8081"

    from core.hardware import should_use_gpu
    n_gpu = "999" if should_use_gpu() else "0"

    LLAMACPP_IMAGE = "dustynv/llama_cpp:0.3.8-r36.4.0-cu128-24.04"

    loop = asyncio.get_event_loop()

    def _start():
        # Check if Docker image exists
        if shutil.which("docker"):
            check = subprocess.run(
                ["docker", "image", "inspect", LLAMACPP_IMAGE],
                capture_output=True, timeout=5,
            )
            if check.returncode != 0:
                raise RuntimeError(
                    f"Docker image '{LLAMACPP_IMAGE}' not found. "
                    f"Run: docker pull {LLAMACPP_IMAGE}"
                )

        # Stop existing container if any
        subprocess.run(["docker", "rm", "-f", "selena-llama"], capture_output=True, timeout=10)

        model_rel = gguf_path.replace("/root/.ollama/", "")

        cmd = [
            "docker", "run", "-d",
            "--name", "selena-llama",
            "--network", "host",
        ]

        if should_use_gpu():
            cmd += ["--runtime", "nvidia"]

        cmd += [
            "-v", "selenacore_ollama_data:/root/.ollama:ro",
            LLAMACPP_IMAGE,
            "python3", "-m", "llama_cpp.server",
            "--model", f"/root/.ollama/{model_rel}",
            "--host", "0.0.0.0", "--port", port,
            "--n_gpu_layers", n_gpu,
            "--n_ctx", "2048",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(result.stderr[:300])
        return "started"

    try:
        msg = await loop.run_in_executor(None, _start)
        import time
        time.sleep(3)
        return {"status": "ok", "message": msg, "model": model, "gpu_layers": n_gpu}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/llamacpp/stop")
async def llamacpp_stop() -> dict[str, Any]:
    """Stop llama.cpp server."""
    loop = asyncio.get_event_loop()

    def _stop():
        subprocess.run(["docker", "stop", "selena-llama"], capture_output=True, timeout=15)
        subprocess.run(["docker", "rm", "selena-llama"], capture_output=True, timeout=5)

    try:
        await loop.run_in_executor(None, _stop)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


async def _find_gguf_for_model(model_name: str) -> str | None:
    """Find GGUF blob for an Ollama model by checking manifests."""
    ollama_models = Path("/root/.ollama/models")
    if not ollama_models.exists():
        # Try volume path
        for p in [Path("/var/lib/selena/ollama/models"), Path("/root/.ollama/models")]:
            if p.exists():
                ollama_models = p
                break

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
            resp = await client.delete(f"{ollama_url}/api/delete", json={"name": req.model})
            resp.raise_for_status()
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ================================================================== #
#  Vosk Dynamic Model Catalog                                          #
# ================================================================== #

class _VoskHTMLParser(HTMLParser):
    """Parse the Vosk models page HTML table."""

    def __init__(self) -> None:
        super().__init__()
        self.models: list[dict[str, Any]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cells: list[str] = []
        self._current_link: str | None = None
        self._current_text = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attrs_dict = dict(attrs)
        if tag == "table":
            self._in_table = True
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._cells = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._current_text = ""
            self._current_link = None
        elif tag == "a" and self._in_cell:
            href = attrs_dict.get("href", "")
            if href:
                self._current_link = href

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._in_table = False
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if len(self._cells) >= 2:
                self._parse_row(self._cells)
        elif tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            self._cells.append(self._current_text.strip())

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_text += data

    def _parse_row(self, cells: list[str]) -> None:
        name = cells[0].strip()
        if not name or name.lower() == "model" or not name.startswith("vosk-model"):
            return

        size_str = cells[1].strip() if len(cells) > 1 else ""
        notes = cells[2].strip() if len(cells) > 2 else ""

        # Parse size (e.g., "40M", "1.8G")
        size_mb = 0
        try:
            if "G" in size_str.upper():
                size_mb = int(float(size_str.upper().replace("G", "").strip()) * 1024)
            elif "M" in size_str.upper():
                size_mb = int(float(size_str.upper().replace("M", "").strip()))
        except ValueError:
            pass

        # Extract language from model name
        lang = "unknown"
        parts = name.split("-")
        for p in parts:
            if len(p) == 2 and p.isalpha():
                lang = p
                break
        # Handle country codes like "en-us", "en-in"
        for i, p in enumerate(parts):
            if p in ("en", "ru", "uk", "de", "fr", "es", "pt", "cn", "ja", "ko", "ar", "fa", "tr", "pl", "nl", "it", "ca", "hi", "vn", "kz"):
                lang = p
                break

        url = f"https://alphacephei.com/vosk/models/{name}.zip"

        self.models.append({
            "id": name,
            "name": name,
            "lang": lang,
            "size_mb": size_mb,
            "notes": notes,
            "url": url,
        })


@router.get("/stt/catalog")
async def stt_catalog() -> dict[str, Any]:
    """Fetch dynamic Vosk model catalog from alphacephei.com. Cached 24h."""
    cache_file = CACHE_DIR / "vosk_catalog.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Check cache
    if cache_file.exists():
        import time
        age = time.time() - cache_file.stat().st_mtime
        if age < 86400:  # 24h
            try:
                return json.loads(cache_file.read_text())
            except Exception:
                pass

    # Fetch from remote
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get("https://alphacephei.com/vosk/models")
            resp.raise_for_status()
            html = resp.text

        parser = _VoskHTMLParser()
        parser.feed(html)
        models = parser.models

        result = {"models": models, "source": "remote"}
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
        return result

    except Exception as exc:
        logger.warning("Vosk catalog fetch failed: %s", exc)
        # Try cache even if expired
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                data["source"] = "cache"
                return data
            except Exception:
                pass
        return {"models": [], "source": "error", "error": str(exc)}


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

_download_state: dict[str, dict] = {}


@router.post("/stt/download")
async def stt_download(req: ModelIdRequest) -> dict[str, Any]:
    """Download a Vosk STT model."""
    model_id = req.model
    if model_id in _download_state and _download_state[model_id].get("running"):
        return {"status": "already_downloading"}

    # Get URL from catalog cache or construct it
    url = f"https://alphacephei.com/vosk/models/{model_id}.zip"
    cache_file = CACHE_DIR / "vosk_catalog.json"
    if cache_file.exists():
        try:
            catalog = json.loads(cache_file.read_text())
            for m in catalog.get("models", []):
                if m["id"] == model_id:
                    url = m.get("url", url)
                    break
        except Exception:
            pass

    _download_state[model_id] = {"running": True, "progress": "starting"}
    asyncio.create_task(_download_vosk_model(model_id, url))
    return {"status": "started", "model": model_id}


async def _download_vosk_model(model_id: str, url: str) -> None:
    """Download and extract Vosk model zip."""
    import zipfile
    VOSK_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = VOSK_MODELS_DIR / f"{model_id}.zip"
    try:
        _download_state[model_id]["progress"] = "downloading"
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
            async with client.stream("GET", url, follow_redirects=True) as resp:
                resp.raise_for_status()
                with open(zip_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

        _download_state[model_id]["progress"] = "extracting"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _extract_vosk_zip, zip_path, VOSK_MODELS_DIR, model_id)

        _download_state[model_id] = {"running": False, "progress": "done", "success": True}
        logger.info("Vosk model %s downloaded successfully", model_id)
    except Exception as exc:
        logger.error("Vosk model download failed: %s", exc)
        _download_state[model_id] = {"running": False, "progress": "error", "success": False, "error": str(exc)}
    finally:
        zip_path.unlink(missing_ok=True)


def _extract_vosk_zip(zip_path: Path, dest_dir: Path, model_id: str) -> None:
    """Extract Vosk model zip and rename to expected directory name."""
    import zipfile
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)

    target = dest_dir / model_id
    if not target.exists():
        extracted_dirs = [d for d in dest_dir.iterdir() if d.is_dir() and d.name != model_id]
        for d in extracted_dirs:
            if model_id.replace("-", "") in d.name.replace("-", "") or d.name.startswith("vosk-model"):
                d.rename(target)
                break


@router.get("/stt/download-progress/{model_id}")
async def stt_download_progress(model_id: str) -> dict[str, Any]:
    """Check download progress for a Vosk model."""
    return _download_state.get(model_id, {"running": False, "progress": "idle"})


@router.post("/stt/delete")
async def stt_delete(req: ModelIdRequest) -> dict[str, Any]:
    """Delete an installed Vosk model."""
    model_path = VOSK_MODELS_DIR / req.model
    if model_path.is_dir():
        shutil.rmtree(model_path)
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Model not found")


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
    try:
        if provider == "ollama":
            try: await llamacpp_stop()
            except Exception: pass
            try: await ollama_start()
            except Exception: pass

        elif provider == "llamacpp":
            try: await ollama_stop()
            except Exception: pass
            if model:
                try: await llamacpp_start({"model": model})
                except Exception: pass

        else:
            # Cloud — stop both to free GPU
            try: await ollama_stop()
            except Exception: pass
            try: await llamacpp_stop()
            except Exception: pass
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
