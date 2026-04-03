"""
system_modules/voice_core/webrtc_stream.py — WebRTC audio stream → Whisper STT

Provides a FastAPI WebSocket endpoint that:
  1. Accepts browser audio stream via WebSocket (raw PCM frames)
  2. Pipes frames into the STT provider (Whisper)
  3. Returns transcription results as JSON messages
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])

SAMPLE_RATE = 16000
CHUNK_DURATION_SEC = 3.0  # transcribe every 3 seconds of audio


@router.websocket("/stream")
async def audio_stream_ws(websocket: WebSocket) -> None:
    """WebSocket endpoint for browser audio streaming.

    Browser sends: raw 16-bit PCM frames (mono, 16kHz)
    Server sends:  JSON {"text": "...", "lang": "...", "final": false/true}
    """
    await websocket.accept()
    logger.info("WebRTC audio stream connected")

    from core.stt import create_stt_provider
    provider = create_stt_provider()
    buffer = b""
    chunk_bytes = int(SAMPLE_RATE * CHUNK_DURATION_SEC * 2)  # 16-bit PCM

    try:
        while True:
            data = await asyncio.wait_for(websocket.receive_bytes(), timeout=30.0)

            if data == b"END":
                # Client signals end of stream
                if buffer:
                    result = await provider.transcribe(buffer, SAMPLE_RATE)
                    if result.text:
                        await websocket.send_json({"text": result.text, "lang": result.lang, "final": True})
                break

            buffer += data

            # Transcribe in sliding chunks
            while len(buffer) >= chunk_bytes:
                chunk = buffer[:chunk_bytes]
                buffer = buffer[chunk_bytes:]
                result = await provider.transcribe(chunk, SAMPLE_RATE)
                if result.text:
                    await websocket.send_json({"text": result.text, "lang": result.lang, "final": False})

    except WebSocketDisconnect:
        logger.info("WebRTC audio stream disconnected")
    except asyncio.TimeoutError:
        logger.info("WebRTC audio stream timeout")
    except Exception as e:
        logger.error("WebRTC stream error: %s", e)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
