#!/usr/bin/env bash
# ------------------------------------------------------------------
# Install native Piper TTS as a systemd service.
#
# Usage:  sudo bash scripts/install-piper-service.sh
#
# What it does:
#   1. Checks Python dependencies (piper-tts, aiohttp)
#   2. Creates models directory  ~/.local/share/piper/models
#   3. Installs & enables piper-tts.service (auto-start on boot)
# ------------------------------------------------------------------
set -euo pipefail

# ── resolve real user (not root when run via sudo) ────────────────
REAL_USER="${SUDO_USER:-$(whoami)}"
REAL_HOME=$(eval echo "~${REAL_USER}")
SELENA_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODELS_DIR="${REAL_HOME}/.local/share/piper/models"

echo "══════════════════════════════════════════════════"
echo "  Piper TTS — native service installer"
echo "══════════════════════════════════════════════════"
echo "  User:       ${REAL_USER}"
echo "  Home:       ${REAL_HOME}"
echo "  Selena:     ${SELENA_DIR}"
echo "  Models dir: ${MODELS_DIR}"
echo "══════════════════════════════════════════════════"

# ── check dependencies ────────────────────────────────────────────
echo ""
echo "[1/4] Checking Python dependencies..."

PIP="${REAL_HOME}/.local/bin/pip3"
[ -x "$PIP" ] || PIP="$(which pip3 2>/dev/null || true)"

MISSING=()
for pkg in piper-tts aiohttp; do
    if ! su - "${REAL_USER}" -c "python3 -c \"import ${pkg//-/_}\"" &>/dev/null; then
        MISSING+=("$pkg")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "  Installing missing packages: ${MISSING[*]}"
    su - "${REAL_USER}" -c "pip3 install --user ${MISSING[*]}"
else
    echo "  All dependencies OK."
fi

# ── GPU check (informational) ────────────────────────────────────
GPU_STATUS="CPU"
if su - "${REAL_USER}" -c "python3 -c \"import onnxruntime; exit(0 if 'CUDAExecutionProvider' in onnxruntime.get_available_providers() else 1)\"" &>/dev/null; then
    GPU_STATUS="GPU (CUDAExecutionProvider)"
fi
echo "  Runtime: ${GPU_STATUS}"

# ── create models directory ───────────────────────────────────────
echo ""
echo "[2/4] Creating models directory..."
install -d -o "${REAL_USER}" -g "${REAL_USER}" "${MODELS_DIR}"
echo "  ${MODELS_DIR} — OK"

MODEL_COUNT=$(find "${MODELS_DIR}" -name "*.onnx" 2>/dev/null | wc -l)
echo "  Models found: ${MODEL_COUNT}"
if [ "$MODEL_COUNT" -eq 0 ]; then
    echo "  ⚠  No voice models yet. Upload via admin panel or place .onnx files in ${MODELS_DIR}"
fi

# ── install systemd service ───────────────────────────────────────
echo ""
echo "[3/4] Installing systemd service..."

sed \
    -e "s|__USER__|${REAL_USER}|g" \
    -e "s|__HOME__|${REAL_HOME}|g" \
    -e "s|__SELENA_DIR__|${SELENA_DIR}|g" \
    "${SELENA_DIR}/scripts/piper-tts.service" \
    > /etc/systemd/system/piper-tts.service

systemctl daemon-reload
systemctl enable piper-tts

# ── start service ─────────────────────────────────────────────────
echo ""
echo "[4/4] Starting piper-tts service..."
systemctl restart piper-tts
sleep 2

if systemctl is-active --quiet piper-tts; then
    echo ""
    echo "  ✓ piper-tts is running on port 5100 (${GPU_STATUS})"
    echo ""
    echo "  Test:  curl http://localhost:5100/health"
    echo "  Logs:  journalctl -u piper-tts -f"
else
    echo ""
    echo "  ✗ Service failed to start. Check logs:"
    echo "    journalctl -u piper-tts -n 20 --no-pager"
    echo ""
    journalctl -u piper-tts -n 10 --no-pager 2>/dev/null || true
    exit 1
fi
