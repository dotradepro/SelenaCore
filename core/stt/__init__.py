"""core.stt — STT provider abstraction for SelenaCore.

Supports Vosk backend (offline, streaming, grammar-aware).
Provider is selected via core.yaml `stt.provider` or auto-detected from available models.
"""
from core.stt.base import STTProvider, STTResult
from core.stt.factory import create_stt_provider

__all__ = ["STTProvider", "STTResult", "create_stt_provider"]
