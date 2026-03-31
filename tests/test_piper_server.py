"""tests/test_piper_server.py — tests for scripts/piper-server.py"""
from __future__ import annotations

import importlib
import struct
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture()
def ps():
    """Import piper-server module (hyphenated name requires importlib)."""
    spec = importlib.util.spec_from_file_location(
        "piper_server",
        str(Path(__file__).parent.parent / "scripts" / "piper-server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    # Prevent actual onnxruntime import during module load
    with patch.dict("sys.modules", {"onnxruntime": MagicMock()}):
        spec.loader.exec_module(mod)
    return mod


class TestDeviceDetection:
    """Test CPU/GPU device resolution."""

    def test_resolve_cpu(self, ps):
        assert ps._resolve_device("cpu") is False

    def test_resolve_gpu_without_cuda(self, ps):
        with patch.object(ps, "_detect_cuda", return_value=False):
            assert ps._resolve_device("gpu") is False

    def test_resolve_gpu_with_cuda(self, ps):
        with patch.object(ps, "_detect_cuda", return_value=True):
            assert ps._resolve_device("gpu") is True

    def test_resolve_auto_no_cuda(self, ps):
        with patch.object(ps, "_detect_cuda", return_value=False):
            assert ps._resolve_device("auto") is False

    def test_resolve_auto_with_cuda(self, ps):
        with patch.object(ps, "_detect_cuda", return_value=True):
            assert ps._resolve_device("auto") is True


class TestPcmToWav:
    """Test WAV header generation."""

    def test_wav_header_riff(self, ps):
        pcm = b"\x00\x01" * 100
        wav = ps._pcm_to_wav(pcm, sample_rate=22050)
        assert wav[:4] == b"RIFF"
        assert wav[8:12] == b"WAVE"
        assert wav[36:40] == b"data"

    def test_wav_data_size(self, ps):
        pcm = b"\x00" * 200
        wav = ps._pcm_to_wav(pcm)
        data_size = struct.unpack_from("<I", wav, 40)[0]
        assert data_size == 200

    def test_wav_sample_rate(self, ps):
        wav = ps._pcm_to_wav(b"\x00" * 100, sample_rate=44100)
        rate = struct.unpack_from("<I", wav, 24)[0]
        assert rate == 44100


class TestHealthEndpoint:
    """Test /health response format."""

    @pytest.mark.asyncio
    async def test_health_fields(self, ps):
        import json
        ps.USE_CUDA = False
        ps.MODELS_DIR = "/tmp/test"
        ps.DEFAULT_VOICE = "test-voice"
        ps._voices.clear()

        resp = await ps.handle_health(MagicMock())
        body = json.loads(resp.body)

        assert body["status"] == "ok"
        assert body["device"] in ("cpu", "gpu")
        assert isinstance(body["cuda_available"], bool)
        assert "models_dir" in body
        assert "loaded_voices" in body

    @pytest.mark.asyncio
    async def test_health_gpu_mode(self, ps):
        import json
        ps.USE_CUDA = True
        resp = await ps.handle_health(MagicMock())
        body = json.loads(resp.body)
        assert body["device"] == "gpu"

    @pytest.mark.asyncio
    async def test_health_cpu_mode(self, ps):
        import json
        ps.USE_CUDA = False
        resp = await ps.handle_health(MagicMock())
        body = json.loads(resp.body)
        assert body["device"] == "cpu"
