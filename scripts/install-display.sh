#!/bin/bash
# scripts/install-display.sh — Install & start the SelenaCore TTY1 display service
#
# Run ONCE on the Raspberry Pi host (as root or with sudo):
#   sudo bash /home/selena/SelenaCore/scripts/install-display.sh
#
# What it does:
#   1. Disables getty@tty1 so our display owns TTY1
#   2. Installs smarthome-display.service
#   3. Enables and starts it

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_SRC="$REPO_DIR/smarthome-display.service"
SERVICE_DST="/etc/systemd/system/smarthome-display.service"

log() { echo "[install-display] $*"; }

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run as root: sudo bash scripts/install-display.sh"
    exit 1
fi

log "Stopping and masking getty@tty1 (TTY1 will be used by SelenaCore)..."
systemctl stop getty@tty1.service 2>/dev/null || true
systemctl disable getty@tty1.service 2>/dev/null || true
systemctl mask getty@tty1.service 2>/dev/null || true

log "Installing $SERVICE_DST ..."
cp "$SERVICE_SRC" "$SERVICE_DST"

log "Enabling and starting smarthome-display.service..."
systemctl daemon-reload
systemctl enable smarthome-display.service
systemctl restart smarthome-display.service

sleep 3
log "Done. Service status:"
systemctl status smarthome-display.service --no-pager -l || true
