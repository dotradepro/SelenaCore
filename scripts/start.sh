#!/bin/bash
# Start Core API (:7070) and UI Core (:80 HTTP + :443 HTTPS) in parallel
set -euo pipefail

# Auto-generate self-signed TLS certificate if not present
TLS_CERT="/secure/tls/selena.crt"
TLS_KEY="/secure/tls/selena.key"
if [ ! -f "$TLS_CERT" ] || [ ! -f "$TLS_KEY" ]; then
  echo "[start.sh] Generating self-signed TLS certificate..."
  python3 scripts/generate_https_cert.py && echo "[start.sh] TLS cert generated OK" \
    || echo "[start.sh] WARNING: cert generation failed, HTTPS will be disabled"
fi

# --- GPU/CUDA Detection ---
# Skip detection if already set via docker-compose environment
if [ -n "${SELENA_GPU_AVAILABLE:-}" ]; then
  GPU_FOUND=$SELENA_GPU_AVAILABLE
  GPU_TYPE=${SELENA_GPU_TYPE:-none}
else
  GPU_FOUND=0
  GPU_TYPE=none

  # Method 1: nvidia-smi works fully
  if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null 2>&1; then
    GPU_FOUND=1
    [ -f /etc/nv_tegra_release ] && GPU_TYPE=jetson || GPU_TYPE=discrete
  fi

  # Method 2: Jetson — check /dev/nvidia0 + libcuda.so (nvidia-smi may not work in container)
  if [ "$GPU_FOUND" = "0" ] && [ -e /dev/nvidia0 ]; then
    if ldconfig -p 2>/dev/null | grep -q libcuda || [ -f /usr/lib/aarch64-linux-gnu/tegra/libcuda.so ]; then
      GPU_FOUND=1
      GPU_TYPE=jetson
    fi
  fi
fi

export SELENA_GPU_AVAILABLE=$GPU_FOUND
export SELENA_GPU_TYPE=$GPU_TYPE

if [ "$GPU_FOUND" = "1" ]; then
  echo "[start.sh] GPU detected: $GPU_TYPE"
else
  echo "[start.sh] No GPU detected, CPU-only mode"
fi
# Persist GPU info to core.yaml
python -c "
from core.config_writer import update_config
import os
update_config('hardware', 'gpu_detected', os.environ.get('SELENA_GPU_AVAILABLE') == '1')
update_config('hardware', 'gpu_type', os.environ.get('SELENA_GPU_TYPE', 'none'))
" 2>/dev/null || true

# --- Piper TTS device mode ---
# Explicit user toggle in core.yaml wins: voice.tts.primary.cuda=true|false
# (set by wizard TTS step or /tts/dual-config). Unset → fall back to
# auto-detected GPU presence.
if [ "$GPU_FOUND" = "1" ]; then
  PIPER_DEVICE_AUTO=gpu
else
  PIPER_DEVICE_AUTO=cpu
fi
PIPER_DEVICE=$(python3 -c "
from core.config_writer import get_nested
v = get_nested('voice.tts.primary.cuda', None)
if v is True:   print('gpu')
elif v is False: print('cpu')
else:           print('')
" 2>/dev/null || echo "")
[ -z "$PIPER_DEVICE" ] && PIPER_DEVICE=$PIPER_DEVICE_AUTO
export PIPER_DEVICE
echo "[start.sh] Piper device: $PIPER_DEVICE (auto=$PIPER_DEVICE_AUTO)"

# --- Audio Mixer Setup ---
echo "[start.sh] Configuring ALSA audio mixer..."
python -c "
from core.audio_mixer import get_mixer
mixer = get_mixer()
mixer.initialize()
" 2>/dev/null && echo "[start.sh] Audio mixer OK" || echo "[start.sh] WARNING: audio mixer setup failed"

# --- Piper TTS HTTP server ---
# Supervised as a subprocess so core + TTS share the same container +
# process group (single `docker compose restart core` cycles both). Host
# port 5100 is exposed via network_mode: host in docker-compose.yml.
# Upgrading from the old host-side piper-tts.service? Run once:
#   sudo bash scripts/migrate_piper_to_container.sh
: "${PIPER_PORT:=5100}"
: "${PIPER_MODELS_DIR:=/var/lib/selena/models/piper}"
export PIPER_MODELS_DIR
echo "[start.sh] Starting piper-server.py on :$PIPER_PORT (device=$PIPER_DEVICE)..."
python3 scripts/piper-server.py \
  --port "$PIPER_PORT" \
  --host 127.0.0.1 \
  --device "$PIPER_DEVICE" \
  --models-dir "$PIPER_MODELS_DIR" &
PIPER_PID=$!
echo "[start.sh] Piper PID=$PIPER_PID"

# --- Unified server: Core API + SPA on port 80 (no separate UI proxy) ---
echo "[start.sh] Starting SelenaCore on :80 (HTTP, unified API + SPA)..."
python -m uvicorn core.main:app --host 0.0.0.0 --port 80 --no-access-log &
CORE_PID=$!

# HTTPS: lightweight TLS proxy via Python (no second uvicorn = saves ~1.5 GB RAM)
# Forwards TLS connections on :443 to the main HTTP server on :80
TLS_CERT="/secure/tls/selena.crt"
TLS_KEY="/secure/tls/selena.key"
HTTPS_PID=""
if [ -f "$TLS_CERT" ] && [ -f "$TLS_KEY" ]; then
  echo "[start.sh] Starting TLS proxy :443 → :80..."
  python3 -c "
import asyncio, ssl, sys

async def handle(reader, writer):
    try:
        r2, w2 = await asyncio.open_connection('127.0.0.1', 80)
        async def pipe(src, dst):
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except Exception:
                pass
            finally:
                try: dst.close()
                except: pass
        await asyncio.gather(pipe(reader, w2), pipe(r2, writer))
    except Exception:
        pass
    finally:
        writer.close()

async def main():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain('$TLS_CERT', '$TLS_KEY')
    srv = await asyncio.start_server(handle, '0.0.0.0', 443, ssl=ctx)
    print('[tls-proxy] Listening on :443', flush=True)
    async with srv:
        await srv.serve_forever()

asyncio.run(main())
" &
  HTTPS_PID=$!
  echo "[start.sh] TLS proxy PID=$HTTPS_PID"
else
  echo "[start.sh] No TLS certificate found, HTTPS disabled"
fi

echo "[start.sh] Core PID=$CORE_PID"

# If any process exits, kill all others and exit
wait -n "$CORE_PID" "$PIPER_PID" ${HTTPS_PID:+"$HTTPS_PID"}
EXIT_CODE=$?

echo "[start.sh] Process exited (code $EXIT_CODE), shutting down..."
kill "$CORE_PID" "$PIPER_PID" ${HTTPS_PID:+"$HTTPS_PID"} 2>/dev/null || true
exit $EXIT_CODE
