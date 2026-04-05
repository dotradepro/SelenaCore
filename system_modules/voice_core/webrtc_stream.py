"""
system_modules/voice_core/webrtc_stream.py — WebSocket live STT stream (server-side mic).

Server captures audio from Jetson mic (arecord), feeds to Vosk in real-time,
streams partial/final results to browser via WebSocket.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])

SAMPLE_RATE = 16000
CHUNK_BYTES = 8000  # 250ms at 16kHz 16-bit mono — good balance for Vosk


@router.websocket("/stream")
async def audio_stream_ws(websocket: WebSocket) -> None:
    """Live STT: server mic → Vosk → WebSocket → browser."""
    await websocket.accept()
    logger.info("Live STT stream connected")

    from core.stt.vosk_provider import VoskProvider

    # Get provider from voice-core
    provider = None
    vc = None
    try:
        from core.module_loader.sandbox import get_sandbox
        vc = get_sandbox().get_in_process_module("voice-core")
        if vc and hasattr(vc, "_stt_provider"):
            provider = vc._stt_provider
    except Exception:
        pass

    if not provider:
        from core.stt import create_stt_provider
        provider = create_stt_provider()

    is_vosk = isinstance(provider, VoskProvider)
    if not is_vosk or not provider._model:
        await websocket.send_json({"type": "error", "text": "Vosk model not loaded"})
        await websocket.close()
        return

    import vosk

    # State
    result_mode = "both"
    lang = provider.lang
    stop_event = asyncio.Event()

    # Create dedicated recognizer
    rec = vosk.KaldiRecognizer(provider._model, SAMPLE_RATE)
    rec.SetWords(True)
    rec.SetPartialWords(True)

    # Get input device
    input_device = None
    try:
        if vc:
            input_device = vc._get_input_device()
    except Exception:
        pass

    # Pause voice-core audio loop
    mic_paused = False
    try:
        if vc and hasattr(vc, "_mic_test_active"):
            vc._mic_test_active = True
            mic_paused = True
            proc_arecord = getattr(vc, "_arecord_proc", None)
            if proc_arecord and proc_arecord.poll() is None:
                proc_arecord.kill()
                proc_arecord.wait(timeout=2)
            await asyncio.sleep(0.2)
    except Exception:
        pass

    # Start arecord
    cmd = ["arecord", "-t", "raw", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1"]
    if input_device:
        cmd.extend(["-D", input_device])

    arecord_proc = None
    try:
        arecord_proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        await websocket.send_json({"type": "error", "text": f"arecord failed: {e}"})
        await websocket.close()
        if mic_paused and vc:
            vc._mic_test_active = False
        return

    logger.info("Live STT: arecord started (device=%s)", input_device or "default")

    async def read_controls():
        """Read control messages from browser."""
        nonlocal result_mode, rec
        try:
            while not stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                    ctrl = json.loads(raw)
                    cmd_type = ctrl.get("cmd", "")

                    if cmd_type == "stop":
                        stop_event.set()
                        return

                    if cmd_type == "mode":
                        result_mode = ctrl.get("value", "both")
                        await websocket.send_json({"type": "control", "mode": result_mode})

                    if cmd_type == "grammar":
                        grammar_on = ctrl.get("enabled", False)
                        if grammar_on and provider._grammar_phrases:
                            grammar_json = json.dumps(
                                provider._grammar_phrases + ["[unk]"],
                                ensure_ascii=False,
                            )
                            rec = vosk.KaldiRecognizer(provider._model, SAMPLE_RATE, grammar_json)
                        else:
                            rec = vosk.KaldiRecognizer(provider._model, SAMPLE_RATE)
                            rec.SetWords(True)
                            rec.SetPartialWords(True)
                        await websocket.send_json({"type": "control", "grammar": grammar_on})

                except asyncio.TimeoutError:
                    continue
        except (WebSocketDisconnect, Exception):
            stop_event.set()

    async def process_audio():
        """Read audio from arecord, feed to Vosk, send results."""
        loop = asyncio.get_running_loop()
        try:
            while not stop_event.is_set():
                data = await loop.run_in_executor(None, arecord_proc.stdout.read, CHUNK_BYTES)
                if not data or stop_event.is_set():
                    break

                if rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    text = result.get("text", "").strip()
                    if text and text != "[unk]" and result_mode in ("final", "both"):
                        try:
                            await websocket.send_json({"text": text, "type": "final", "lang": lang})
                        except Exception:
                            break
                else:
                    if result_mode in ("partial", "both"):
                        partial = json.loads(rec.PartialResult())
                        text = partial.get("partial", "").strip()
                        if text and text != "[unk]":
                            try:
                                await websocket.send_json({"text": text, "type": "partial", "lang": lang})
                            except Exception:
                                break
        except Exception as e:
            logger.debug("Live STT audio loop: %s", e)
        finally:
            stop_event.set()

    try:
        # Run control reader and audio processor in parallel
        await asyncio.gather(
            read_controls(),
            process_audio(),
        )

        # Final result
        try:
            final = json.loads(rec.FinalResult())
            text = final.get("text", "").strip()
            if text and text != "[unk]":
                await websocket.send_json({"text": text, "type": "final", "lang": lang})
        except Exception:
            pass

    except Exception as e:
        logger.error("Live STT error: %s", e)
    finally:
        if arecord_proc:
            try:
                arecord_proc.kill()
                arecord_proc.wait(timeout=2)
            except Exception:
                pass

        if mic_paused and vc:
            try:
                vc._mic_test_active = False
            except Exception:
                pass

        try:
            await websocket.close()
        except Exception:
            pass

        logger.info("Live STT stream ended")
