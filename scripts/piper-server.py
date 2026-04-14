#!/usr/bin/env python3
"""
Piper TTS HTTP server — runs natively on host with persistent models in memory.

Models are loaded once and kept warm — synthesis takes 100-300ms (CPU) or 30-80ms (GPU).

Usage:
    python3 scripts/piper-server.py [--port 5100] [--device auto|cpu|gpu]

Requires: pip install piper-tts aiohttp
GPU:      pip install onnxruntime-gpu  (or build via scripts/build-onnxruntime-gpu.sh)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import struct
import threading
from pathlib import Path

from aiohttp import web

logging.basicConfig(level=logging.INFO, format="[piper-server] %(message)s")
logger = logging.getLogger("piper-server")

_default_models = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "piper", "models",
)
MODELS_DIR = os.environ.get("PIPER_MODELS_DIR", _default_models)
DEFAULT_VOICE = os.environ.get("PIPER_VOICE", "uk_UA-ukrainian_tts-medium")

# Device mode: "cpu", "gpu", or "auto"
DEVICE_MODE = os.environ.get("PIPER_DEVICE", "auto")
USE_CUDA = False

# Persistent model cache: voice_id → PiperVoice
_voices: dict[str, object] = {}
_voices_lock = threading.Lock()


def _detect_cuda() -> bool:
    """Check if CUDAExecutionProvider is available in onnxruntime."""
    try:
        import onnxruntime
        return "CUDAExecutionProvider" in onnxruntime.get_available_providers()
    except Exception:
        return False


def _resolve_device(mode: str) -> bool:
    """Resolve device mode to use_cuda boolean."""
    if mode == "gpu":
        if _detect_cuda():
            return True
        logger.warning("GPU requested but CUDAExecutionProvider not available — using CPU")
        return False
    if mode == "cpu":
        return False
    # auto
    return _detect_cuda()


def _get_voice(voice_id: str):
    """Load or return cached PiperVoice. Thread-safe."""
    if voice_id in _voices:
        return _voices[voice_id]
    with _voices_lock:
        if voice_id in _voices:
            return _voices[voice_id]
        model_path = str(Path(MODELS_DIR) / f"{voice_id}.onnx")
        if not Path(model_path).exists():
            return None
        import piper
        device_label = "GPU" if USE_CUDA else "CPU"
        logger.info("Loading model: %s (%s)", voice_id, device_label)
        voice = piper.PiperVoice.load(model_path, use_cuda=USE_CUDA)
        _voices[voice_id] = voice
        logger.info("Model loaded: %s (rate=%d, %s)", voice_id, voice.config.sample_rate, device_label)
        return voice


def _synthesize_pcm(voice, text: str, length_scale: float, noise_scale: float,
                    noise_w_scale: float, sentence_silence: float,
                    speaker: int, volume: float) -> bytes:
    """Synthesize text to raw PCM bytes using in-memory model."""
    import re
    from piper.config import SynthesisConfig
    cfg = SynthesisConfig(
        speaker_id=speaker if speaker > 0 else None,
        length_scale=length_scale,
        noise_scale=noise_scale,
        noise_w_scale=noise_w_scale,
        volume=volume,
    )

    # Split into sentences and insert silence between them
    sentences = re.split(r'(?<=[.!?…])\s+|\n+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        sentences = [text]

    sample_rate = voice.config.sample_rate
    silence_samples = int(sample_rate * sentence_silence)
    silence_bytes = b'\x00\x00' * silence_samples

    parts = []
    for i, sent in enumerate(sentences):
        chunks = voice.synthesize(sent, syn_config=cfg)
        for c in chunks:
            parts.append(c.audio_int16_bytes)
        if i < len(sentences) - 1 and silence_samples > 0:
            parts.append(silence_bytes)

    return b"".join(parts)


def _pcm_to_wav(pcm: bytes, sample_rate: int = 22050) -> bytes:
    """Wrap raw PCM s16le mono into WAV container."""
    data_size = len(pcm)
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + data_size, b'WAVE', b'fmt ', 16,
        1, 1, sample_rate, sample_rate * 2, 2, 16,
        b'data', data_size,
    )
    return header + pcm


async def handle_synthesize(request: web.Request) -> web.Response:
    """POST /synthesize — JSON body → WAV audio response."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    text = body.get("text", "").strip()
    if not text:
        return web.json_response({"error": "empty text"}, status=400)

    voice_id = body.get("voice", DEFAULT_VOICE)
    voice = _get_voice(voice_id)
    if voice is None:
        return web.json_response({"error": f"voice not found: {voice_id}"}, status=404)

    length_scale = float(body.get("length_scale", 1.0))
    noise_scale = float(body.get("noise_scale", 0.667))
    noise_w_scale = float(body.get("noise_w_scale", 0.8))
    sentence_silence = float(body.get("sentence_silence", 0.2))
    speaker = int(body.get("speaker", 0))
    volume = float(body.get("volume", 1.0))

    loop = asyncio.get_event_loop()
    try:
        pcm = await loop.run_in_executor(
            None, _synthesize_pcm, voice, text,
            length_scale, noise_scale, noise_w_scale, sentence_silence, speaker, volume,
        )
    except Exception as e:
        logger.error("Synthesis error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

    if not pcm:
        return web.json_response({"error": "empty audio"}, status=500)

    sample_rate = voice.config.sample_rate
    wav = _pcm_to_wav(pcm, sample_rate)

    return web.Response(
        body=wav,
        content_type="audio/wav",
        headers={"Content-Disposition": "inline"},
    )


async def handle_synthesize_raw(request: web.Request) -> web.Response:
    """POST /synthesize/raw — JSON body → raw PCM s16le (for streaming to paplay)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    text = body.get("text", "").strip()
    if not text:
        return web.json_response({"error": "empty text"}, status=400)

    voice_id = body.get("voice", DEFAULT_VOICE)
    voice = _get_voice(voice_id)
    if voice is None:
        return web.json_response({"error": f"voice not found: {voice_id}"}, status=404)

    length_scale = float(body.get("length_scale", 1.0))
    noise_scale = float(body.get("noise_scale", 0.667))
    noise_w_scale = float(body.get("noise_w_scale", 0.8))
    sentence_silence = float(body.get("sentence_silence", 0.2))
    speaker = int(body.get("speaker", 0))
    volume = float(body.get("volume", 1.0))

    loop = asyncio.get_event_loop()
    try:
        pcm = await loop.run_in_executor(
            None, _synthesize_pcm, voice, text,
            length_scale, noise_scale, noise_w_scale, sentence_silence, speaker, volume,
        )
    except Exception as e:
        logger.error("Synthesis error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

    sample_rate = voice.config.sample_rate

    return web.Response(
        body=pcm,
        content_type="audio/pcm",
        headers={
            "X-Audio-Rate": str(sample_rate),
            "X-Audio-Channels": "1",
            "X-Audio-Format": "s16le",
        },
    )


async def handle_health(request: web.Request) -> web.Response:
    cuda_available = _detect_cuda()
    return web.json_response({
        "status": "ok",
        "device": "gpu" if USE_CUDA else "cpu",
        "cuda_available": cuda_available,
        "models_dir": MODELS_DIR,
        "default_voice": DEFAULT_VOICE,
        "loaded_voices": list(_voices.keys()),
    })


async def handle_device(request: web.Request) -> web.Response:
    """POST /device — switch runtime device: {"device": "auto"|"cpu"|"gpu"}.

    Clears the voice cache so subsequent /synthesize calls reload the
    default voice on the new device. First call after a switch pays a
    one-shot load penalty (~1-2 sec per voice).
    """
    global USE_CUDA, DEVICE_MODE
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    mode = str(body.get("device", "auto"))
    if mode not in ("auto", "cpu", "gpu"):
        return web.json_response({"error": "device must be auto|cpu|gpu"}, status=400)
    new_cuda = _resolve_device(mode)
    if new_cuda == USE_CUDA and mode == DEVICE_MODE:
        return web.json_response({
            "status": "noop",
            "device": "gpu" if USE_CUDA else "cpu",
            "mode": DEVICE_MODE,
            "cuda_available": _detect_cuda(),
        })
    with _voices_lock:
        _voices.clear()
    USE_CUDA = new_cuda
    DEVICE_MODE = mode
    logger.info("Runtime device switch: mode=%s use_cuda=%s", mode, USE_CUDA)
    # Warm the default voice so the next /synthesize call doesn't pay
    # the load penalty on its critical path.
    if DEFAULT_VOICE:
        try:
            _get_voice(DEFAULT_VOICE)
        except Exception as exc:
            logger.warning("Warmup failed after device switch: %s", exc)
    return web.json_response({
        "status": "ok",
        "device": "gpu" if USE_CUDA else "cpu",
        "mode": DEVICE_MODE,
        "cuda_available": _detect_cuda(),
    })


async def handle_voices(request: web.Request) -> web.Response:
    """GET /voices — list installed voice models."""
    models_path = Path(MODELS_DIR)
    if not models_path.is_dir():
        return web.json_response([])
    voices = [
        {"id": f.stem, "model": f.name, "loaded": f.stem in _voices}
        for f in sorted(models_path.iterdir())
        if f.is_file() and f.suffix == ".onnx"
    ]
    return web.json_response(voices)


def main():
    global MODELS_DIR, USE_CUDA, DEVICE_MODE

    parser = argparse.ArgumentParser(description="Piper TTS HTTP server")
    parser.add_argument("--port", type=int, default=5100)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--models-dir", default=MODELS_DIR)
    parser.add_argument("--device", default=DEVICE_MODE, choices=["auto", "cpu", "gpu"],
                        help="Device mode: auto (detect GPU), cpu, gpu")
    parser.add_argument("--preload", default="", help="Comma-separated voice IDs to preload")
    args = parser.parse_args()

    MODELS_DIR = args.models_dir
    DEVICE_MODE = args.device
    USE_CUDA = _resolve_device(DEVICE_MODE)

    device_label = "GPU (CUDAExecutionProvider)" if USE_CUDA else "CPU"
    logger.info("Starting on %s:%d — %s", args.host, args.port, device_label)
    logger.info("Models: %s", MODELS_DIR)

    # Preload voices into memory
    if args.preload:
        for vid in args.preload.split(","):
            vid = vid.strip()
            if vid:
                _get_voice(vid)
    elif DEFAULT_VOICE:
        _get_voice(DEFAULT_VOICE)

    app = web.Application()
    app.router.add_post("/synthesize", handle_synthesize)
    app.router.add_post("/synthesize/raw", handle_synthesize_raw)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/device", handle_device)
    app.router.add_get("/voices", handle_voices)

    web.run_app(app, host=args.host, port=args.port, print=logger.info)


if __name__ == "__main__":
    main()
