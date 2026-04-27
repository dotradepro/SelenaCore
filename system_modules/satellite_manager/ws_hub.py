"""WebSocket hub for connected ESP32 satellites.

Each satellite holds exactly one persistent WebSocket to this endpoint.
Frames are binary-packed per `protocol.py`. Multiple frames can coexist in a
single WS message, so we use a per-session buffer and peel frames until the
buffer is drained.

Session lifecycle:
    connect → accept (validate JWT + device_id)
        → load registry row (404 if unknown)
        → push CONFIG frame
        → loop: parse frames, handle each
    disconnect → mark offline in registry
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import Query, WebSocket, WebSocketDisconnect

from .auth import verify_token
from .protocol import (
    Flags,
    Frame,
    MsgType,
    make_config,
    make_pong,
    make_state,
    make_tts_chunk,
    make_tts_end,
)

if TYPE_CHECKING:
    from .audio_bridge import AudioBridge
    from .satellite_registry import SatelliteRegistry

logger = logging.getLogger(__name__)

SEND_TIMEOUT_S = 1.0


@dataclass
class SatelliteSession:
    device_id: str
    location: str | None
    ws: WebSocket
    state: str = "idle"         # idle | listening | processing | speaking
    volume: int = 75
    muted: bool = False
    firmware: str = ""
    rssi: int = 0
    last_heartbeat: float = field(default_factory=time.time)
    active_session_id: str | None = None
    # Mic-test intercept: when set, WAKE + AUDIO_CHUNK + AUDIO_END are
    # captured into `_mic_test_buffer` instead of relayed to the audio
    # bridge, and the result is signalled via `_mic_test_future`.
    _mic_test_future: "asyncio.Future | None" = None
    _mic_test_buffer: bytearray = field(default_factory=bytearray)
    _mic_test_started_at: float = 0.0


class SatelliteWSHub:
    """Owns every connected satellite's WS + routes frames in/out."""

    def __init__(
        self,
        registry: "SatelliteRegistry",
        audio_bridge: "AudioBridge",
        hub_secret: str,
    ) -> None:
        self._registry = registry
        self._audio_bridge = audio_bridge
        self._hub_secret = hub_secret
        self._sessions: dict[str, SatelliteSession] = {}      # device_id → session
        self._session_by_sid: dict[str, SatelliteSession] = {}  # audio session_id → session
        self._sessions_lock = asyncio.Lock()

    async def handle_connection(
        self,
        ws: WebSocket,
        token: str = Query(...),
        device_id: str = Query(...),
    ) -> None:
        # 1. Validate token BEFORE accept so unauthorized clients never see a 101
        if not verify_token(token, device_id, self._hub_secret):
            await ws.close(code=4001, reason="unauthorized")
            return

        # 2. Must already be registered (BLE provisioning created the row)
        device = await self._registry.get(device_id)
        if not device:
            await ws.close(code=4004, reason="not registered")
            return

        await ws.accept()

        session = SatelliteSession(
            device_id=device_id,
            location=device.get("location"),
            ws=ws,
            volume=device.get("state", {}).get("volume", 75),
            muted=device.get("state", {}).get("muted", False),
        )

        # 3. If the satellite reconnects, drop the stale socket first
        async with self._sessions_lock:
            old = self._sessions.get(device_id)
            if old:
                try:
                    await old.ws.close()
                except Exception:
                    pass
            self._sessions[device_id] = session

        await self._registry.set_online(device_id, True)
        logger.info("Satellite connected: %s (location=%s)", device_id, session.location)

        await self._send_config(session, device)

        try:
            await self._receive_loop(session)
        except WebSocketDisconnect:
            logger.info("Satellite disconnected: %s", device_id)
        except Exception:
            logger.exception("Satellite WS error: %s", device_id)
        finally:
            await self._cleanup_session(session)

    async def _receive_loop(self, session: SatelliteSession) -> None:
        buffer = bytearray()
        while True:
            data = await session.ws.receive_bytes()
            buffer.extend(data)
            while len(buffer) >= 4:
                try:
                    frame, consumed = Frame.unpack(bytes(buffer))
                except ValueError:
                    break
                del buffer[:consumed]
                await self._handle_frame(session, frame)

    async def _handle_frame(self, session: SatelliteSession, frame: Frame) -> None:
        try:
            if frame.msg_type == MsgType.WAKE_DETECTED:
                # Mic-test mode: capture this entire wake session locally
                # (no audio_bridge, no voice pipeline). Tell the satellite
                # we're listening so its LED/UI reflects reality.
                if session._mic_test_future is not None:
                    session._mic_test_buffer.clear()
                    session._mic_test_started_at = time.time()
                    session.state = "listening"
                    await self._safe_send(session, make_state("listening"))
                    return

                sid = str(uuid.uuid4())
                session.active_session_id = sid
                session.state = "listening"
                self._session_by_sid[sid] = session
                await self._audio_bridge.on_wake(
                    session_id=sid,
                    device_id=session.device_id,
                    location=session.location,
                )
                await self._safe_send(session, make_state("listening"))

            elif frame.msg_type == MsgType.AUDIO_CHUNK:
                if session._mic_test_future is not None:
                    session._mic_test_buffer.extend(frame.payload)
                    return
                sid = session.active_session_id
                if sid:
                    await self._audio_bridge.on_audio_chunk(sid, frame.payload)

            elif frame.msg_type == MsgType.AUDIO_END:
                if session._mic_test_future is not None:
                    self._finish_mic_test(session, reason="audio_end")
                    return
                sid = session.active_session_id
                if sid:
                    session.state = "processing"
                    await self._safe_send(session, make_state("processing"))
                    await self._audio_bridge.on_audio_end(sid)

            elif frame.msg_type == MsgType.BUTTON_EVENT:
                payload = _parse_json(frame.payload)
                if payload.get("button") == "mute":
                    session.muted = payload.get("state") == "on"
                    await self._registry.update_state(
                        session.device_id, {"muted": session.muted},
                    )

            elif frame.msg_type == MsgType.HEARTBEAT:
                payload = _parse_json(frame.payload)
                session.last_heartbeat = time.time()
                session.rssi = int(payload.get("rssi", 0))
                session.firmware = str(payload.get("firmware", session.firmware))
                await self._registry.update_state(session.device_id, {
                    "rssi": session.rssi,
                    "online": True,
                })
                await self._safe_send(session, make_pong())

            elif frame.msg_type == MsgType.PING:
                await self._safe_send(session, make_pong())

            else:
                logger.debug("Satellite %s: ignored frame %s", session.device_id, frame.msg_type)
        except Exception:
            logger.exception("Satellite %s: error handling %s", session.device_id, frame.msg_type)

    # ── Outbound ─────────────────────────────────────────────────

    async def send_tts_chunk(
        self, session_id: str, pcm_data: bytes, sample_rate: int | None = None,
    ) -> None:
        session = self._session_by_sid.get(session_id)
        if not session:
            return
        # On the first chunk of a TTS burst, advertise the audio format so
        # the ESP32 DAC can be configured before it receives raw PCM bytes.
        # Piper outputs 22050 Hz by default; mic input is 16 kHz.
        if session.state != "speaking":
            session.state = "speaking"
            meta: dict = {}
            if sample_rate:
                meta["sample_rate"] = sample_rate
            await self._safe_send(session, make_state("speaking", **meta))
        await self._safe_send(session, make_tts_chunk(pcm_data))

    async def send_tts_end(
        self, session_id: str, keep_session_open: bool = False,
    ) -> None:
        """Terminate the TTS burst for `session_id`.

        keep_session_open=True keeps the satellite's mic active after TTS
        ends — used for multi-turn clarifications where voice-core expects
        the satellite to keep streaming audio until it gets the user's
        reply. The session stays in _session_by_sid so subsequent
        AUDIO_CHUNK frames keep routing to the same session_id.
        """
        if keep_session_open:
            session = self._session_by_sid.get(session_id)
            if session:
                await self._safe_send(session, make_tts_end())
                await self._safe_send(session, make_state("listening"))
                session.state = "listening"
            return

        session = self._session_by_sid.pop(session_id, None)
        if session:
            await self._safe_send(session, make_tts_end())
            await self._safe_send(session, make_state("idle"))
            session.state = "idle"
            session.active_session_id = None

    async def send_state(self, session_id: str, state: str) -> None:
        session = self._session_by_sid.get(session_id)
        if session:
            session.state = state
            await self._safe_send(session, make_state(state))

    async def push_config(self, device_id: str, body: dict[str, Any]) -> None:
        """Forward a PATCH /satellites/{id} body to the live WS as a CONFIG frame."""
        session = self._sessions.get(device_id)
        if session:
            await self._safe_send(session, make_config(body))

    # ── Lifecycle helpers ────────────────────────────────────────

    def is_online(self, device_id: str) -> bool:
        return device_id in self._sessions

    def get_stale_sessions(self, timeout_s: float) -> list[SatelliteSession]:
        now = time.time()
        return [s for s in self._sessions.values() if now - s.last_heartbeat > timeout_s]

    async def drop_session(self, device_id: str) -> None:
        async with self._sessions_lock:
            session = self._sessions.pop(device_id, None)
        if session:
            if session.active_session_id:
                self._session_by_sid.pop(session.active_session_id, None)
            try:
                await session.ws.close()
            except Exception:
                pass

    # ── Mic test ─────────────────────────────────────────────────
    #
    # Real mic test without protocol changes: arm an intercept on the
    # device's session, prompt the user (UI-side) to say the wake word,
    # and capture the entire wake→audio_chunk*→audio_end burst into a
    # buffer. Return a diagnostic summary (duration, sample count, RMS).

    async def arm_mic_test(
        self, device_id: str, timeout_s: float = 15.0, sample_rate: int = 16000,
    ) -> dict:
        session = self._sessions.get(device_id)
        if session is None:
            return {"status": "offline"}
        if session._mic_test_future is not None:
            return {"status": "busy"}

        loop = asyncio.get_event_loop()
        session._mic_test_future = loop.create_future()
        session._mic_test_buffer.clear()
        session._mic_test_started_at = 0.0

        try:
            try:
                await asyncio.wait_for(session._mic_test_future, timeout=timeout_s)
            except asyncio.TimeoutError:
                self._finish_mic_test(session, reason="timeout")
                # finish sets the future — now read it
            result = session._mic_test_future.result() if session._mic_test_future.done() else {}
        finally:
            session._mic_test_future = None
            # Restore session state for the satellite UI
            try:
                await self._safe_send(session, make_state("idle"))
            except Exception:
                pass

        audio = bytes(session._mic_test_buffer)
        session._mic_test_buffer.clear()
        n_samples = len(audio) // 2
        duration_ms = int(n_samples * 1000 / sample_rate) if sample_rate else 0
        rms = 0.0
        if audio:
            try:
                import audioop
                rms = float(audioop.rms(audio, 2))
            except Exception:
                rms = 0.0
        return {
            **result,
            "samples": n_samples,
            "duration_ms": duration_ms,
            "rms": rms,
            "rssi": session.rssi,
            "firmware": session.firmware,
        }

    def _finish_mic_test(self, session: SatelliteSession, reason: str) -> None:
        fut = session._mic_test_future
        if fut is None or fut.done():
            return
        fut.set_result({"status": reason})

    async def close_all(self) -> None:
        async with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        self._session_by_sid.clear()
        for session in sessions:
            try:
                await session.ws.close()
            except Exception:
                pass

    async def _cleanup_session(self, session: SatelliteSession) -> None:
        async with self._sessions_lock:
            # Only remove if it's still the same session (reconnect may have replaced it)
            if self._sessions.get(session.device_id) is session:
                self._sessions.pop(session.device_id, None)
        if session.active_session_id:
            self._session_by_sid.pop(session.active_session_id, None)
        await self._registry.set_online(session.device_id, False)

    async def _send_config(self, session: SatelliteSession, device: dict) -> None:
        await self._safe_send(session, make_config({
            "location": device.get("location"),
            "volume": device.get("state", {}).get("volume", 75),
            "muted": device.get("state", {}).get("muted", False),
            "wake_word_enabled": True,
            "wake_word_threshold": 0.5,
        }))

    async def _safe_send(self, session: SatelliteSession, payload: bytes) -> None:
        """Send with a timeout so a stuck ESP32 can't wedge the hub task."""
        try:
            await asyncio.wait_for(session.ws.send_bytes(payload), timeout=SEND_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("Satellite %s: send timeout, closing", session.device_id)
            try:
                await session.ws.close()
            except Exception:
                pass
        except Exception:
            logger.exception("Satellite %s: send error", session.device_id)


def _parse_json(payload: bytes) -> dict:
    try:
        return json.loads(payload.decode("utf-8")) if payload else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
