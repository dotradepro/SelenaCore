#!/bin/bash
# Start llama.cpp server with model from argument.
#
# Usage: llamacpp-start.sh <model_path> [port] [gpu_layers] [n_ctx]
#
# Environment overrides:
#   LLAMACPP_GPU_LAYERS  — number of GPU layers (default: 999 = all, 0 = CPU only)
#   LLAMACPP_N_CTX       — context window size (default: 512)
#
# Auto-detects GPU: if /dev/nvidia0 is missing, forces GPU_LAYERS=0.

set -euo pipefail

MODEL="${1:-}"
PORT="${2:-8081}"
GPU_LAYERS="${3:-${LLAMACPP_GPU_LAYERS:-999}}"
N_CTX="${4:-${LLAMACPP_N_CTX:-512}}"

if [ -z "$MODEL" ]; then
    echo "Usage: $0 <model_path> [port] [gpu_layers] [n_ctx]"
    exit 1
fi

# Auto-detect GPU — if no NVIDIA device, force CPU
if [ "$GPU_LAYERS" != "0" ] && [ ! -e /dev/nvidia0 ]; then
    echo "[llamacpp] No GPU detected (/dev/nvidia0 missing) — using CPU only"
    GPU_LAYERS=0
fi

# Auto-detect CUDA path
for cuda_dir in /usr/local/cuda/bin /usr/local/cuda-*/bin; do
    [ -d "$cuda_dir" ] && export PATH="$cuda_dir:$PATH" && break
done

# Auto-detect CUDA libraries
for lib_dir in /usr/local/cuda/lib64 /usr/local/cuda-*/targets/*/lib /usr/lib/aarch64-linux-gnu/tegra; do
    [ -d "$lib_dir" ] && export LD_LIBRARY_PATH="$lib_dir:${LD_LIBRARY_PATH:-}"
done

# Include user pip packages if available
if [ -d "$HOME/.local/lib" ]; then
    PYVER=$(python3 -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
    [ -d "$HOME/.local/lib/$PYVER/site-packages" ] && export PYTHONPATH="$HOME/.local/lib/$PYVER/site-packages:${PYTHONPATH:-}"
    export PATH="$HOME/.local/bin:$PATH"
fi

DEVICE="GPU ($GPU_LAYERS layers)"
[ "$GPU_LAYERS" = "0" ] && DEVICE="CPU"
echo "[llamacpp] Model: $(basename "$MODEL"), Device: $DEVICE, Context: $N_CTX"

exec python3 -m llama_cpp.server \
    --model "$MODEL" \
    --host 0.0.0.0 --port "$PORT" \
    --n_gpu_layers "$GPU_LAYERS" \
    --n_ctx "$N_CTX" \
    --n_batch 128
