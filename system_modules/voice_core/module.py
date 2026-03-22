"""
system_modules/voice_core/module.py — In-process SystemModule wrapper.

Runs inside the core process via importlib — NOT a separate uvicorn subprocess.
Integrates STT, TTS, wake word, speaker ID, privacy mode, and voice history.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from core.module_loader.system_module import SystemModule

logger = logging.getLogger(__name__)


# ── Request models ───────────────────────────────────────────────────────────

class VoiceConfigRequest(BaseModel):
    stt_model: str | None = None
    tts_voice: str | None = None
    wake_word_model: str | None = None
    wake_word_threshold: float | None = Field(None, ge=0.1, le=1.0)
    privacy_mode: bool | None = None
    speaker_threshold: float | None = Field(None, ge=0.3, le=1.0)


class TranscribeRequest(BaseModel):
    sample_rate: int = 16000


class SynthesizeRequest(BaseModel):
    text: str
    voice: str | None = None


class EnrollSpeakerRequest(BaseModel):
    user_id: str


class VoiceCoreModule(SystemModule):
    name = "voice-core"

    def __init__(self) -> None:
        super().__init__()
        self._stt = None
        self._tts = None
        self._wake_word = None
        self._speaker_id = None
        self._voice_history = None
        self._privacy_task: asyncio.Task | None = None
        self._config: dict[str, Any] = {
            "stt_model": os.getenv("VOSK_MODEL", "vosk-model-small-uk"),
            "tts_voice": os.getenv("PIPER_VOICE", "uk_UA-ukrainian_tts-medium"),
            "wake_word_model": os.getenv("WAKE_WORD_MODEL", "hey_selena"),
            "wake_word_threshold": float(os.getenv("WAKE_WORD_THRESHOLD", "0.5")),
            "privacy_mode": False,
            "speaker_threshold": float(os.getenv("SPEAKER_THRESHOLD", "0.75")),
        }

    async def _on_wake_word(self, wake_word: str, score: float) -> None:
        """Callback when wake word is detected."""
        await self.publish("voice.wake_word", {
            "wake_word": wake_word,
            "score": score,
        })

    async def _on_privacy_change(self, enabled: bool) -> None:
        """Callback when privacy mode changes."""
        event_type = "voice.privacy_on" if enabled else "voice.privacy_off"
        await self.publish(event_type, {"privacy_mode": enabled})
        if self._wake_word:
            self._wake_word.set_privacy_mode(enabled)

    async def _on_voice_event(self, event: Any) -> None:
        """Handle voice.speak events from other modules."""
        if event.type == "voice.speak" and self._tts:
            text = event.payload.get("text", "")
            if text:
                await self._tts.synthesize(text)
                await self.publish("voice.speak_done", {"text": text})

    async def start(self) -> None:
        from system_modules.voice_core.stt import get_stt
        from system_modules.voice_core.tts import get_tts
        from system_modules.voice_core.wake_word import get_wake_word_detector
        from system_modules.voice_core.speaker_id import get_speaker_id
        from system_modules.voice_core.voice_history import get_voice_history
        from system_modules.voice_core.privacy import on_privacy_change

        self._stt = get_stt(self._config["stt_model"])
        self._tts = get_tts(self._config["tts_voice"])
        self._wake_word = get_wake_word_detector()
        self._speaker_id = get_speaker_id()
        self._voice_history = get_voice_history()

        # Wire up callbacks
        self._wake_word.on_wake_word(self._on_wake_word)
        on_privacy_change(self._on_privacy_change)

        # Subscribe to voice.speak events
        self.subscribe(["voice.speak"], self._on_voice_event)

        # Start wake word detector (non-blocking)
        try:
            await self._wake_word.start()
        except Exception as exc:
            logger.warning("Wake word detector failed to start: %s", exc)

        # Start GPIO privacy listener
        from system_modules.voice_core.privacy import gpio_listener_loop
        self._privacy_task = asyncio.create_task(gpio_listener_loop())

        await self.publish("module.started", {"name": self.name})
        logger.info("VoiceCoreModule started")

    async def stop(self) -> None:
        if self._wake_word:
            await self._wake_word.stop()
        if self._privacy_task:
            self._privacy_task.cancel()
            await asyncio.gather(self._privacy_task, return_exceptions=True)
            self._privacy_task = None
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})
        logger.info("VoiceCoreModule stopped")

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        # ── Health ────────────────────────────────────────────────────────

        @router.get("/health")
        async def health() -> dict:
            return {"status": "ok", "module": svc.name}

        # ── Config ────────────────────────────────────────────────────────

        @router.get("/config")
        async def get_config() -> JSONResponse:
            from system_modules.voice_core.privacy import is_privacy_mode
            cfg = dict(svc._config)
            cfg["privacy_mode"] = is_privacy_mode()
            return JSONResponse(cfg)

        @router.post("/config")
        async def update_config(req: VoiceConfigRequest) -> JSONResponse:
            from system_modules.voice_core.privacy import set_privacy_mode

            updates = req.model_dump(exclude_none=True)
            for k, v in updates.items():
                if k == "privacy_mode":
                    await set_privacy_mode(v)
                else:
                    svc._config[k] = v

            # Apply wake word threshold if changed
            if "wake_word_threshold" in updates and svc._wake_word:
                svc._wake_word.threshold = updates["wake_word_threshold"]
            if "speaker_threshold" in updates:
                import system_modules.voice_core.speaker_id as sid
                sid.SIMILARITY_THRESHOLD = updates["speaker_threshold"]

            return JSONResponse({"ok": True, "config": svc._config})

        # ── Privacy ───────────────────────────────────────────────────────

        @router.get("/privacy")
        async def get_privacy() -> JSONResponse:
            from system_modules.voice_core.privacy import is_privacy_mode
            return JSONResponse({"privacy_mode": is_privacy_mode()})

        @router.post("/privacy/toggle")
        async def toggle_privacy() -> JSONResponse:
            from system_modules.voice_core.privacy import toggle_privacy_mode
            new_state = await toggle_privacy_mode()
            return JSONResponse({"privacy_mode": new_state})

        # ── Audio devices ─────────────────────────────────────────────────

        @router.get("/audio/devices")
        async def list_audio_devices() -> JSONResponse:
            from system_modules.voice_core.audio_manager import detect_audio_devices
            devices = detect_audio_devices()
            return JSONResponse({
                "inputs": [{"id": d.id, "name": d.name, "type": d.type} for d in devices.inputs],
                "outputs": [{"id": d.id, "name": d.name, "type": d.type} for d in devices.outputs],
            })

        # ── STT ───────────────────────────────────────────────────────────

        @router.get("/stt/status")
        async def stt_status() -> JSONResponse:
            return JSONResponse({
                "model": svc._config["stt_model"],
                "available": svc._stt is not None,
            })

        # ── TTS ───────────────────────────────────────────────────────────

        @router.get("/tts/voices")
        async def list_voices() -> JSONResponse:
            if svc._tts is None:
                raise HTTPException(503, "TTS not ready")
            return JSONResponse({"voices": svc._tts.list_voices()})

        @router.post("/tts/test")
        async def test_tts(req: SynthesizeRequest) -> JSONResponse:
            if svc._tts is None:
                raise HTTPException(503, "TTS not ready")
            wav_bytes = await svc._tts.synthesize(req.text, req.voice)
            if not wav_bytes:
                raise HTTPException(500, "Synthesis failed")
            return JSONResponse({"ok": True, "size_bytes": len(wav_bytes)})

        # ── Speaker ID ────────────────────────────────────────────────────

        @router.get("/speakers")
        async def list_speakers() -> JSONResponse:
            if svc._speaker_id is None:
                raise HTTPException(503, "Speaker ID not ready")
            return JSONResponse({"speakers": svc._speaker_id.list_enrolled()})

        @router.delete("/speakers/{user_id}")
        async def remove_speaker(user_id: str) -> JSONResponse:
            if svc._speaker_id is None:
                raise HTTPException(503, "Speaker ID not ready")
            ok = svc._speaker_id.remove_enrollment(user_id)
            if not ok:
                raise HTTPException(404, "Speaker not found")
            return JSONResponse({"ok": True, "removed": user_id})

        # ── Wake word ─────────────────────────────────────────────────────

        @router.get("/wakeword/status")
        async def wakeword_status() -> JSONResponse:
            return JSONResponse({
                "model": svc._config["wake_word_model"],
                "threshold": svc._config["wake_word_threshold"],
                "running": svc._wake_word._running if svc._wake_word else False,
            })

        # ── Voice history ─────────────────────────────────────────────────

        @router.get("/history")
        async def get_history(limit: int = 50) -> JSONResponse:
            if svc._voice_history is None:
                raise HTTPException(503, "Voice history not ready")
            records = await svc._voice_history.get_recent(min(limit, 200))
            return JSONResponse({"records": records})

        # ── Widget & settings HTML ────────────────────────────────────────

        @router.get("/widget", response_class=HTMLResponse)
        async def widget() -> HTMLResponse:
            f = Path(__file__).parent / "widget.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>widget.html not found</p>")

        @router.get("/settings", response_class=HTMLResponse)
        async def settings_page() -> HTMLResponse:
            f = Path(__file__).parent / "settings.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>settings.html not found</p>")

        # WebSocket endpoint for audio streaming
        @router.websocket("/stream")
        async def audio_stream_ws(websocket: WebSocket) -> None:
            from system_modules.voice_core.webrtc_stream import audio_stream_ws as _handler
            await _handler(websocket)

        return router
