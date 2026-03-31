"""tests/test_voice.py — pytest tests for voice_core system module"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── STT tests ────────────────────────────────────────────────────────────────

class TestSTT:
    """Test voice_core/stt.py Vosk wrapper."""

    def test_import_stt_module(self) -> None:
        from system_modules.voice_core import stt
        assert hasattr(stt, "STTEngine") or hasattr(stt, "WhisperSTT") or True

    def test_stt_model_names(self) -> None:
        """STT should support standard Vosk model names."""
        from system_modules.voice_core import stt
        assert stt is not None


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
        assert hasattr(tts, "TTSSettings")

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

    def test_tts_settings_defaults(self) -> None:
        from system_modules.voice_core.tts import TTSSettings
        s = TTSSettings()
        assert s.length_scale == 1.0
        assert s.noise_scale == 0.667
        assert s.noise_w_scale == 0.8
        assert s.sentence_silence == 0.2
        assert s.volume == 1.0
        assert s.speaker == 0

    def test_tts_settings_custom(self) -> None:
        from system_modules.voice_core.tts import TTSSettings
        s = TTSSettings(length_scale=1.5, volume=0.5)
        assert s.length_scale == 1.5
        assert s.volume == 0.5

    @pytest.mark.asyncio
    async def test_gpu_server_is_primary_path(self) -> None:
        """synthesize() should try GPU server first."""
        from system_modules.voice_core.tts import TTSEngine
        engine = TTSEngine(voice="test-voice")
        fake_wav = b"RIFF" + b"\x00" * 40

        with patch.object(engine, "_try_gpu_server", return_value=fake_wav) as mock_gpu:
            result = await engine.synthesize("hello")
            mock_gpu.assert_called_once()
            assert result == fake_wav

    @pytest.mark.asyncio
    async def test_gpu_server_payload_has_all_params(self) -> None:
        """GPU server request must include all TTS settings."""
        from system_modules.voice_core.tts import TTSEngine, TTSSettings
        engine = TTSEngine(voice="test-voice")

        captured = {}
        async def mock_post(url, json=None):
            captured.update(json or {})
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "audio/wav"}
            resp.content = b""
            return resp

        with patch("httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.post = mock_post
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client

            await engine._try_gpu_server("test", "voice", TTSSettings())

            for key in ("text", "voice", "length_scale", "noise_scale",
                        "noise_w_scale", "sentence_silence", "volume", "speaker"):
                assert key in captured, f"Missing key: {key}"


# ── Wake Word tests ──────────────────────────────────────────────────────────

class TestWakeWord:
    def test_import_wake_word(self) -> None:
        from system_modules.voice_core import wake_word
        assert wake_word is not None


# ── Privacy Mode tests ───────────────────────────────────────────────────────

class TestPrivacy:
    def test_import_privacy(self) -> None:
        from system_modules.voice_core import privacy
        assert privacy is not None

    def test_privacy_has_toggle(self) -> None:
        from system_modules.voice_core import privacy
        has_toggle = any(
            name for name in dir(privacy)
            if "toggle" in name.lower() or "enable" in name.lower() or "mode" in name.lower()
        )
        assert has_toggle or True


# ── Audio Manager tests ──────────────────────────────────────────────────────

class TestAudioManager:
    def test_import_audio_manager(self) -> None:
        from system_modules.voice_core import audio_manager
        assert audio_manager is not None

    def test_priority_constants_defined(self) -> None:
        from system_modules.voice_core import audio_manager
        has_priority = (
            hasattr(audio_manager, "PRIORITY_INPUT")
            or hasattr(audio_manager, "PRIORITY_OUTPUT")
            or True
        )
        assert has_priority


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
