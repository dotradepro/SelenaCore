"""
core/hardware.py — Hardware detection: GPU/CUDA availability.

Single source of truth for all engine code.
Detection runs once at container startup (start.sh sets env vars),
this module reads those vars with fallback to core.yaml config.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

_gpu_cache: dict | None = None


def _detect() -> dict:
    """Detect GPU availability. Cached after first call."""
    global _gpu_cache
    if _gpu_cache is not None:
        return _gpu_cache

    # Fast path: env vars set by start.sh
    env_gpu = os.environ.get("SELENA_GPU_AVAILABLE")
    if env_gpu is not None:
        gpu = env_gpu == "1"
        gpu_type = os.environ.get("SELENA_GPU_TYPE", "none")
        _gpu_cache = {"gpu": gpu, "type": gpu_type}
        return _gpu_cache

    # Fallback: read from config
    try:
        from core.config_writer import get_value
        gpu = get_value("hardware", "gpu_detected", False)
        gpu_type = get_value("hardware", "gpu_type", "none")
        _gpu_cache = {"gpu": bool(gpu), "type": gpu_type or "none"}
        return _gpu_cache
    except Exception:
        pass

    # Runtime detection (slowest path, only if nothing else works)
    gpu = False
    gpu_type = "none"

    # Method 1: nvidia-smi
    try:
        if shutil.which("nvidia-smi"):
            result = subprocess.run(["nvidia-smi"], capture_output=True, timeout=5)
            if result.returncode == 0:
                gpu = True
                gpu_type = "jetson" if os.path.exists("/etc/nv_tegra_release") else "discrete"
    except Exception:
        pass

    # Method 2: Jetson device + libcuda (nvidia-smi may fail in container)
    if not gpu and os.path.exists("/dev/nvidia0"):
        if os.path.exists("/usr/lib/aarch64-linux-gnu/tegra/libcuda.so"):
            gpu = True
            gpu_type = "jetson"
        else:
            try:
                result = subprocess.run(
                    ["ldconfig", "-p"], capture_output=True, text=True, timeout=5
                )
                if "libcuda" in result.stdout:
                    gpu = True
                    gpu_type = "jetson" if os.path.exists("/etc/nv_tegra_release") else "discrete"
            except Exception:
                pass

    _gpu_cache = {"gpu": gpu, "type": gpu_type}
    return _gpu_cache


def is_gpu_available() -> bool:
    """Check if GPU/CUDA is available on this system."""
    return _detect()["gpu"]


def get_gpu_type() -> str:
    """Return GPU type: 'jetson', 'discrete', or 'none'."""
    return _detect()["type"]


def should_use_gpu() -> bool:
    """Check GPU available AND not force-disabled by user."""
    if not is_gpu_available():
        return False

    # Check force_cpu override
    try:
        from core.config_writer import get_value
        if get_value("hardware", "force_cpu", False):
            return False
    except Exception:
        pass

    # Verify onnxruntime actually has CUDA (for Piper)
    return True


def onnxruntime_has_gpu() -> bool:
    """Check if onnxruntime has CUDAExecutionProvider available."""
    try:
        import onnxruntime
        return "CUDAExecutionProvider" in onnxruntime.get_available_providers()
    except Exception:
        return False


def get_hardware_info() -> dict:
    """Return full hardware info dict for API responses."""
    return {
        "gpu_detected": is_gpu_available(),
        "gpu_type": get_gpu_type(),
        "gpu_active": should_use_gpu(),
        "onnxruntime_gpu": onnxruntime_has_gpu(),
        "force_cpu": _get_force_cpu(),
    }


def _get_force_cpu() -> bool:
    try:
        from core.config_writer import get_value
        return bool(get_value("hardware", "force_cpu", False))
    except Exception:
        return False
