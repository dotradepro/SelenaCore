#!/usr/bin/env bash
# migrate_piper_to_container.sh — upgrade helper for SelenaCore.
#
# Before this release Piper TTS ran on the host as piper-tts.service.
# Piper now runs inside the smarthome-core container (see scripts/start.sh
# and Dockerfile.core). This script stops and removes the host-side unit,
# then restarts the container so its built-in Piper supervisor binds :5100.
#
# Run once on each already-deployed target:
#
#   sudo bash scripts/migrate_piper_to_container.sh
#
# Safe to re-run — every step is idempotent.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "[migrate] must be run as root (sudo bash $0)"
  exit 1
fi

UNIT=/etc/systemd/system/piper-tts.service

if systemctl list-unit-files --type=service 2>/dev/null | grep -q '^piper-tts.service'; then
  echo "[migrate] Stopping piper-tts.service on host..."
  systemctl disable --now piper-tts.service || true
else
  echo "[migrate] piper-tts.service not registered — skipping stop/disable"
fi

if [[ -f "$UNIT" ]]; then
  echo "[migrate] Removing $UNIT"
  rm -f "$UNIT"
  systemctl daemon-reload
fi

# Free :5100 if a stale piper process is still holding it (e.g. manual
# start outside systemd). fuser is best-effort — not every distro ships it.
if command -v fuser >/dev/null 2>&1; then
  PIDS=$(fuser 5100/tcp 2>/dev/null | awk '{$1=$1};1')
  if [[ -n "$PIDS" ]]; then
    echo "[migrate] Freeing port 5100 (PIDs: $PIDS)"
    kill $PIDS 2>/dev/null || true
    sleep 1
  fi
fi

# Restart the core container so start.sh re-spawns piper-server.py on :5100.
# docker-compose lives at the repo root; resolve relative to this script.
REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -f "$REPO_ROOT/docker-compose.yml" ]]; then
  echo "[migrate] Restarting smarthome-core container..."
  (cd "$REPO_ROOT" && docker compose restart core)
else
  echo "[migrate] docker-compose.yml not found at $REPO_ROOT — restart the container manually"
fi

echo "[migrate] Done. Verify with: curl -s http://localhost:5100/health"
