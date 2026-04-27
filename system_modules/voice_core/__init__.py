"""system_modules/voice_core/__init__.py

``module_class`` is lazy-imported via PEP 562 so that sub-module imports
(e.g. pulling in audio_session alone for unit tests) don't drag in the full
voice_core.module dependency chain (httpx, vosk, piper, etc.).
"""

__all__ = ["module_class"]


def __getattr__(name: str):
    if name == "module_class":
        from .module import VoiceCoreModule
        return VoiceCoreModule
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
