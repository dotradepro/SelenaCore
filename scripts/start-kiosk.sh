#!/usr/bin/env bash
# scripts/start-kiosk.sh — Launch Chromium in kiosk mode via cage (Wayland)
# Used by smarthome-kiosk.service on devices with HDMI display attached.
set -euo pipefail

UI_URL="${UI_URL:-http://localhost}"

# ── Wait for UI to be reachable ──
echo "[kiosk] Waiting for UI at ${UI_URL} ..."
for i in $(seq 1 60); do
    if curl -sf -o /dev/null "${UI_URL}" 2>/dev/null; then
        echo "[kiosk] UI is ready"
        break
    fi
    sleep 2
done

# ── Chromium flags for kiosk ──
CHROMIUM_FLAGS=(
    --kiosk
    --noerrdialogs
    --disable-infobars
    --disable-session-crashed-bubble
    --disable-translate
    --no-first-run
    --disable-features=Translate
    --check-for-update-interval=31536000
    --autoplay-policy=no-user-gesture-required
    --disable-pinch
    --overscroll-history-navigation=0
    --no-default-browser-check
    --disable-component-update
    --disable-background-networking
    --disable-sync
    --ozone-platform=wayland
    --enable-features=OverlayScrollbar
    --hide-scrollbars
    --start-fullscreen
    --user-data-dir=/tmp/chromium-kiosk
)

# Find chromium binary (chromium or chromium-browser depending on distro)
CHROMIUM_BIN="$(command -v chromium 2>/dev/null || command -v chromium-browser 2>/dev/null)" \
    || { echo "[kiosk] ERROR: chromium not found"; exit 1; }

echo "[kiosk] Starting cage + $CHROMIUM_BIN on ${UI_URL}"
exec cage -- "$CHROMIUM_BIN" "${CHROMIUM_FLAGS[@]}" "${UI_URL}"
