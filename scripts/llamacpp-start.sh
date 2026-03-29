#!/bin/bash
# Start llama.cpp server with model from argument
# Usage: llamacpp-start.sh <model_path> [port] [gpu_layers]
# Works for any user — auto-detects paths

MODEL="${1:-}"
PORT="${2:-8081}"
GPU_LAYERS="${3:-999}"

if [ -z "$MODEL" ]; then
    echo "Usage: $0 <model_path> [port] [gpu_layers]"
    exit 1
fi

# Auto-detect CUDA path
for cuda_dir in /usr/local/cuda/bin /usr/local/cuda-*/bin; do
    [ -d "$cuda_dir" ] && export PATH="$cuda_dir:$PATH" && break
done

# Auto-detect CUDA libraries
for lib_dir in /usr/local/cuda/lib64 /usr/local/cuda-*/targets/*/lib /usr/lib/aarch64-linux-gnu/tegra; do
    [ -d "$lib_dir" ] && export LD_LIBRARY_PATH="$lib_dir:${LD_LIBRARY_PATH}"
done

# Include user pip packages if available
if [ -d "$HOME/.local/lib" ]; then
    PYVER=$(python3 -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
    [ -d "$HOME/.local/lib/$PYVER/site-packages" ] && export PYTHONPATH="$HOME/.local/lib/$PYVER/site-packages:${PYTHONPATH}"
    export PATH="$HOME/.local/bin:$PATH"
fi

exec python3 -m llama_cpp.server \
    --model "$MODEL" \
    --host 0.0.0.0 --port "$PORT" \
    --n_gpu_layers "$GPU_LAYERS" \
    --n_ctx 2048
