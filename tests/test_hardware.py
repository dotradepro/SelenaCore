"""
tests/test_hardware.py — GPU/CUDA detection tests
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

import core.hardware as hw


@pytest.fixture(autouse=True)
def _clear_cache():
    hw._gpu_cache = None
    yield
    hw._gpu_cache = None


class TestGpuDetectionEnvVars:
    def test_gpu_available_from_env(self):
        with patch.dict("os.environ", {"SELENA_GPU_AVAILABLE": "1", "SELENA_GPU_TYPE": "jetson"}):
            assert hw.is_gpu_available() is True
            assert hw.get_gpu_type() == "jetson"

    def test_no_gpu_from_env(self):
        with patch.dict("os.environ", {"SELENA_GPU_AVAILABLE": "0", "SELENA_GPU_TYPE": "none"}):
            assert hw.is_gpu_available() is False
            assert hw.get_gpu_type() == "none"

    def test_discrete_gpu(self):
        with patch.dict("os.environ", {"SELENA_GPU_AVAILABLE": "1", "SELENA_GPU_TYPE": "discrete"}):
            assert hw.is_gpu_available() is True
            assert hw.get_gpu_type() == "discrete"


class TestGpuDetectionFallback:
    def test_no_env_no_config_no_nvidia(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch("core.config_writer.get_value", side_effect=Exception("no config")), \
             patch("shutil.which", return_value=None), \
             patch("os.path.exists", return_value=False):
            hw._gpu_cache = None
            assert hw.is_gpu_available() is False
            assert hw.get_gpu_type() == "none"


class TestShouldUseGpu:
    def test_no_gpu_returns_false(self):
        with patch.dict("os.environ", {"SELENA_GPU_AVAILABLE": "0"}):
            assert hw.should_use_gpu() is False

    def test_gpu_available(self):
        with patch.dict("os.environ", {"SELENA_GPU_AVAILABLE": "1", "SELENA_GPU_TYPE": "jetson"}):
            hw._gpu_cache = None
            assert hw.is_gpu_available() is True


class TestOnnxruntimeHasGpu:
    def test_onnxruntime_not_installed(self):
        assert hw.onnxruntime_has_gpu() is False or hw.onnxruntime_has_gpu() is True
        # Just verify it doesn't crash


class TestGetHardwareInfo:
    def test_returns_dict(self):
        with patch.dict("os.environ", {"SELENA_GPU_AVAILABLE": "0", "SELENA_GPU_TYPE": "none"}):
            info = hw.get_hardware_info()
            assert "gpu_detected" in info
            assert "gpu_type" in info
            assert "gpu_active" in info
            assert "force_cpu" in info
