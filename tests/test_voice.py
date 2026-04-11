"""tests/test_voice.py — pytest tests for voice_core system module"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── STT Provider tests ──────────────────────────────────────────────────────

class TestSTTProvider:
    """Test core/stt/ provider abstraction (Vosk backend)."""

    def test_import_stt_package(self) -> None:
        from core.stt import STTProvider, STTResult, create_stt_provider
        assert STTProvider is not None
        assert STTResult is not None

    def test_stt_result_defaults(self) -> None:
        from core.stt.base import STTResult
        r = STTResult()
        assert r.text == ""
        assert r.lang == "en"
        assert r.confidence == 0.0

    def test_stt_result_with_values(self) -> None:
        from core.stt.base import STTResult
        r = STTResult(text="hello", lang="uk", confidence=0.95)
        assert r.text == "hello"
        assert r.lang == "uk"

    def test_factory_returns_provider(self) -> None:
        """Factory should return a provider (DummyProvider if no Vosk model)."""
        from core.stt import create_stt_provider
        from core.stt.base import STTProvider
        provider = create_stt_provider({"provider": "auto"})
        assert isinstance(provider, STTProvider)

    @pytest.mark.asyncio
    async def test_dummy_provider_returns_empty(self) -> None:
        from core.stt.factory import _DummyProvider
        p = _DummyProvider()
        result = await p.transcribe(b"\x00" * 100, 16000)
        assert result.text == ""
        assert result.lang == "en"

    def test_vosk_provider_import(self) -> None:
        """VoskProvider should be importable."""
        from core.stt.vosk_provider import VoskProvider
        assert VoskProvider is not None

    def test_vosk_provider_status_before_load(self) -> None:
        """VoskProvider status should show not ready before model load."""
        from core.stt.vosk_provider import VoskProvider
        p = VoskProvider(model_path="/nonexistent", lang="en")
        st = p.status()
        assert st["provider"] == "vosk"
        assert st["ready"] is False
        assert st["lang"] == "en"

    def test_vosk_provider_properties(self) -> None:
        """VoskProvider should expose lang, model_path, is_ready, is_loading."""
        from core.stt.vosk_provider import VoskProvider
        p = VoskProvider(model_path="/tmp/test_model", lang="uk")
        assert p.lang == "uk"
        assert p.model_path == "/tmp/test_model"
        assert p.is_ready is False
        assert p.is_loading is False

    @pytest.mark.asyncio
    async def test_vosk_provider_transcribe_without_model(self) -> None:
        """transcribe() should return empty result if model not loaded."""
        from core.stt.vosk_provider import VoskProvider
        p = VoskProvider(model_path="/nonexistent", lang="en")
        result = await p.transcribe(b"\x00" * 1000, 16000)
        assert result.text == ""

    def test_vosk_provider_feed_idle_without_grammar(self) -> None:
        """feed_idle() should return (None, None) without grammar set."""
        from core.stt.vosk_provider import VoskProvider
        p = VoskProvider(model_path="/nonexistent", lang="en")
        partial, final = p.feed_idle(b"\x00" * 1000)
        assert partial is None
        assert final is None

    def test_vosk_provider_feed_listening_without_model(self) -> None:
        """feed_listening() should return (None, None) without model."""
        from core.stt.vosk_provider import VoskProvider
        p = VoskProvider(model_path="/nonexistent", lang="en")
        partial, final = p.feed_listening(b"\x00" * 1000)
        assert partial is None
        assert final is None

    def test_factory_no_whisper_providers(self) -> None:
        """Old Whisper provider files should not exist."""
        assert not Path("core/stt/faster_whisper.py").exists()
        assert not Path("core/stt/whisper_cpp.py").exists()
        assert not Path("core/stt/whisper_trt.py").exists()
        assert not Path("core/stt/openai_stt.py").exists()


# ── TTS tests ────────────────────────────────────────────────────────────────

class TestTTS:
    """Test voice_core/tts.py Piper wrapper."""

    def test_import_tts_module(self) -> None:
        from system_modules.voice_core import tts
        assert tts is not None

    def test_tts_has_expected_interface(self) -> None:
        """TTS module should define TTSEngine and sanitize_for_tts."""
        from system_modules.voice_core import tts
        assert hasattr(tts, "TTSEngine")
        assert hasattr(tts, "sanitize_for_tts")

    def test_sanitize_for_tts_removes_markdown(self) -> None:
        from system_modules.voice_core.tts import sanitize_for_tts
        assert "**" not in sanitize_for_tts("**bold text**")
        assert "`" not in sanitize_for_tts("`code`")
        assert "http" not in sanitize_for_tts("visit https://example.com ok")

    def test_sanitize_for_tts_removes_emoji(self) -> None:
        from system_modules.voice_core.tts import sanitize_for_tts
        result = sanitize_for_tts("hello 😀 world")
        assert "😀" not in result
        assert "hello" in result

    def test_sanitize_for_tts_lowercase(self) -> None:
        from system_modules.voice_core.tts import sanitize_for_tts
        result = sanitize_for_tts("HELLO World")
        assert result == "hello world"

    def test_sanitize_for_tts_empty(self) -> None:
        from system_modules.voice_core.tts import sanitize_for_tts
        assert sanitize_for_tts("") == ""
        assert sanitize_for_tts("   ") == ""


# ── Privacy Mode tests ───────────────────────────────────────────────────────

class TestPrivacy:
    def test_import_privacy(self) -> None:
        from system_modules.voice_core import privacy
        assert privacy is not None


# ── Audio Manager tests ──────────────────────────────────────────────────────

class TestAudioManager:
    def test_import_audio_manager(self) -> None:
        from system_modules.voice_core import audio_manager
        assert audio_manager is not None


# ── Speaker ID tests ─────────────────────────────────────────────────────────

class TestSpeakerID:
    def test_import_speaker_id(self) -> None:
        from system_modules.voice_core import speaker_id
        assert speaker_id is not None


# ── MediaPlayer VLC leak fix tests ───────────────────────────────────────────

class TestMediaPlayerRelease:
    """Test VLC resource cleanup in media_player."""

    def test_player_has_release_method(self) -> None:
        from system_modules.media_player.player import MediaPlayer
        assert hasattr(MediaPlayer, "release")

    @pytest.mark.asyncio
    async def test_play_url_releases_old_media(self) -> None:
        """play_url should release previous media object."""
        from system_modules.media_player.player import MediaPlayer

        player = MediaPlayer.__new__(MediaPlayer)
        player._vlc = MagicMock()
        player._instance = MagicMock()
        player._player = MagicMock()
        player._list_player = MagicMock()
        player._volume = 70
        player._shuffle = False
        player._current_source_type = "unknown"
        player._stub_state = "stopped"
        player._stub_track = None
        player._watchdog_task = None

        old_media = MagicMock()
        player._player.get_media.return_value = old_media
        player._player.play.return_value = 0

        await player.play_url("http://test.stream", "radio")
        old_media.release.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_releases_media(self) -> None:
        """stop() should release current media."""
        from system_modules.media_player.player import MediaPlayer

        player = MediaPlayer.__new__(MediaPlayer)
        player._vlc = MagicMock()
        player._instance = MagicMock()
        player._player = MagicMock()
        player._watchdog_task = None

        media = MagicMock()
        player._player.get_media.return_value = media

        await player.stop()
        player._player.stop.assert_called_once()
        media.release.assert_called_once()

    def test_release_cleans_all_resources(self) -> None:
        """release() should free instance, player, list_player."""
        from system_modules.media_player.player import MediaPlayer

        player = MediaPlayer.__new__(MediaPlayer)
        player._vlc = MagicMock()
        mock_instance = MagicMock()
        mock_player = MagicMock()
        mock_player.get_media.return_value = None
        mock_list_player = MagicMock()
        player._instance = mock_instance
        player._player = mock_player
        player._list_player = mock_list_player

        player.release()
        mock_player.stop.assert_called_once()
        mock_player.release.assert_called_once()
        mock_list_player.release.assert_called_once()
        mock_instance.release.assert_called_once()
        assert player._vlc is None
        assert player._instance is None
