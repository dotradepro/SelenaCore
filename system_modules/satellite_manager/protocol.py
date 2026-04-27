"""Binary frame protocol for ESP32 satellite communication.

Frame layout (little-endian):
    [type: 1B][flags: 1B][length: 2B LE][payload: N bytes]

Total header: 4 bytes. Max payload: 65535 bytes.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from enum import IntEnum


class MsgType(IntEnum):
    # ESP32 → Hub
    AUDIO_CHUNK = 0x01
    WAKE_DETECTED = 0x02
    AUDIO_END = 0x03
    BUTTON_EVENT = 0x04
    HEARTBEAT = 0x05

    # Hub → ESP32
    STATE = 0x10
    TTS_CHUNK = 0x11
    TTS_END = 0x12
    CONFIG = 0x13
    VOLUME = 0x14
    LED = 0x15

    # Bidirectional
    PING = 0x20
    PONG = 0x21


class Flags(IntEnum):
    NONE = 0x00
    JSON = 0x01
    BINARY = 0x02


HEADER_SIZE = 4
HEADER_STRUCT = struct.Struct("<BBH")
MAX_PAYLOAD = 0xFFFF


@dataclass
class Frame:
    msg_type: MsgType
    flags: int
    payload: bytes

    def pack(self) -> bytes:
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError(f"payload too large: {len(self.payload)} > {MAX_PAYLOAD}")
        header = HEADER_STRUCT.pack(int(self.msg_type), int(self.flags), len(self.payload))
        return header + self.payload

    @staticmethod
    def unpack(data: bytes) -> tuple["Frame", int]:
        """Unpack one frame from buffer. Returns (frame, bytes_consumed).

        Raises ValueError if the buffer is too short to hold a full frame.
        """
        if len(data) < HEADER_SIZE:
            raise ValueError("incomplete header")
        msg_type_raw, flags, length = HEADER_STRUCT.unpack_from(data)
        total = HEADER_SIZE + length
        if len(data) < total:
            raise ValueError("incomplete payload")
        payload = bytes(data[HEADER_SIZE:total])
        return Frame(MsgType(msg_type_raw), flags, payload), total


def make_audio_chunk(pcm_data: bytes) -> bytes:
    return Frame(MsgType.AUDIO_CHUNK, Flags.BINARY, pcm_data).pack()


def make_tts_chunk(pcm_data: bytes) -> bytes:
    return Frame(MsgType.TTS_CHUNK, Flags.BINARY, pcm_data).pack()


def make_tts_end() -> bytes:
    return Frame(MsgType.TTS_END, Flags.NONE, b"").pack()


def make_state(state: str, **meta: object) -> bytes:
    """Build a STATE frame. Extra kwargs are merged into the JSON payload
    so callers can advertise metadata like `sample_rate` alongside the
    `state` transition without inventing a new message type. Firmware that
    doesn't know a key simply ignores it.
    """
    data: dict = {"state": state}
    data.update(meta)
    payload = json.dumps(data).encode("utf-8")
    return Frame(MsgType.STATE, Flags.JSON, payload).pack()


def make_config(config: dict) -> bytes:
    payload = json.dumps(config).encode("utf-8")
    return Frame(MsgType.CONFIG, Flags.JSON, payload).pack()


def make_volume(volume: int) -> bytes:
    payload = json.dumps({"volume": volume}).encode("utf-8")
    return Frame(MsgType.VOLUME, Flags.JSON, payload).pack()


def make_led(pattern: str, color: str | None = None) -> bytes:
    data: dict = {"pattern": pattern}
    if color is not None:
        data["color"] = color
    payload = json.dumps(data).encode("utf-8")
    return Frame(MsgType.LED, Flags.JSON, payload).pack()


def make_pong() -> bytes:
    return Frame(MsgType.PONG, Flags.NONE, b"").pack()


def make_ping() -> bytes:
    return Frame(MsgType.PING, Flags.NONE, b"").pack()
