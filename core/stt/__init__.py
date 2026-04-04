"""core.stt — STT provider abstraction for SelenaCore.

Supports multiple Whisper backends: WhisperTRT (Jetson), whisper.cpp, faster-whisper, OpenAI API.
Provider is selected via core.yaml `stt.provider` or auto-detected from hardware.
"""
from core.stt.base import STTProvider, STTResult
from core.stt.factory import create_stt_provider

__all__ = ["STTProvider", "STTResult", "create_stt_provider"]
