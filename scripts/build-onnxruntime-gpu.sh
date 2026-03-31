#!/bin/bash
# Install onnxruntime-gpu for NVIDIA Jetson (aarch64, JetPack 6, CUDA 12.x).
#
# Pre-built wheels are available from NVIDIA Jetson AI Lab index.
# PyPI does NOT have aarch64 GPU wheels — must use Jetson-specific index.
#
# Requirements:
#   - NVIDIA Jetson with JetPack 6.x (CUDA 12.x, cuDNN 9.x)
#   - Python 3.10+
#
# Usage:
#   bash scripts/build-onnxruntime-gpu.sh
#
# What it does:
#   1. Installs onnxruntime-gpu from Jetson AI Lab
#   2. Downgrades numpy to <2 (required compatibility)
#   3. Creates libcudnn.so symlink if missing
#   4. Verifies CUDAExecutionProvider is available

set -euo pipefail

JETSON_INDEX="https://pypi.jetson-ai-lab.io/jp6/cu126"

echo "========================================"
echo "  Install onnxruntime-gpu for Jetson"
echo "========================================"

# Check we're on Jetson
if [ ! -f /etc/nv_tegra_release ]; then
    echo "WARNING: /etc/nv_tegra_release not found — this may not be a Jetson device"
fi

# Step 1: Install onnxruntime-gpu from Jetson AI Lab
echo ""
echo "[1/4] Installing onnxruntime-gpu from Jetson AI Lab..."
pip3 install --user --force-reinstall onnxruntime-gpu \
    --extra-index-url "$JETSON_INDEX"

# Step 2: Downgrade numpy (NumPy 2.x is incompatible with onnxruntime-gpu on Jetson)
echo ""
echo "[2/4] Installing compatible numpy (<2)..."
pip3 install --user "numpy<2"

# Step 3: Create libcudnn.so symlink if missing
echo ""
echo "[3/4] Checking libcudnn.so symlink..."
CUDNN_SO="/usr/lib/aarch64-linux-gnu/libcudnn.so"
CUDNN_SO9="/usr/lib/aarch64-linux-gnu/libcudnn.so.9"
if [ ! -f "$CUDNN_SO" ] && [ -f "$CUDNN_SO9" ]; then
    echo "Creating symlink: $CUDNN_SO -> $CUDNN_SO9"
    sudo ln -sf "$CUDNN_SO9" "$CUDNN_SO"
    sudo ldconfig
else
    echo "libcudnn.so OK"
fi

# Step 4: Verify
echo ""
echo "[4/4] Verifying..."
python3 -c "
import onnxruntime as ort
providers = ort.get_available_providers()
print('Version:', ort.__version__)
print('Providers:', providers)
if 'CUDAExecutionProvider' in providers:
    print('✓ CUDA GPU support is available')
else:
    print('✗ CUDAExecutionProvider NOT found — check CUDA installation')
    exit(1)
" 2>&1 | grep -v "W:onnx"

echo ""
echo "========================================"
echo "  Done. Restart piper-tts to use GPU:"
echo "  sudo systemctl restart piper-tts"
echo "========================================"
