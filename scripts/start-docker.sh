#!/bin/bash
# Auto-detect GPU and start Docker Compose with appropriate config.
# Usage: ./scripts/start-docker.sh [up|down|restart|logs]

set -e
cd "$(dirname "$0")/.."

ACTION="${1:-up -d}"

if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
  echo "[start-docker] GPU detected, using GPU compose override"
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml $ACTION
else
  echo "[start-docker] No GPU, running CPU-only"
  docker compose $ACTION
fi
