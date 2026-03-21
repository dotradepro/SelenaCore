#!/bin/bash
# scripts/start-display.sh — Host-side display launcher for SelenaCore
#
# Runs on the Raspberry Pi HOST (not inside Docker).
# Detects display mode and launches the appropriate UI:
#   kiosk       — cage + Chromium in kiosk mode (Wayland, no DE needed)
#   tty         — Python TUI with QR code + split-panel status on TTY1
#   headless    — nothing (connect via browser over network)
#
# Called by smarthome-display.service after Docker is up and healthy.

set -euo pipefail

UI_URL="${SELENA_UI_URL:-http://localhost:8080}"
COMPOSE_FILE="${COMPOSE_FILE:-/home/selena/SelenaCore/docker-compose.yml}"
LOG="${SELENA_LOG_DIR:-/var/log/selena}/display.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# ── Wait for core container ─────────────────────────────────────────────
wait_for_core() {
    log "Waiting for core container..."
    for i in $(seq 1 30); do
        docker compose -f "$COMPOSE_FILE" ps core 2>/dev/null | grep -qE "running|healthy" && return 0
        log "waiting... ($i)"
        sleep 3
    done
    log "ERROR: core container not ready after 90s"
    return 1
}

# ── Wait for UI HTTP ─────────────────────────────────────────────────────
wait_for_ui() {
    log "Waiting for UI at $UI_URL ..."
    for i in $(seq 1 30); do
        if curl -sf -o /dev/null "$UI_URL" 2>/dev/null; then
            log "UI is up."
            return 0
        fi
        sleep 2
    done
    log "WARNING: UI not reachable after 60s, proceeding anyway"
}

# ── Display mode detection ───────────────────────────────────────────────
detect_mode() {
    # Check if cage + chromium are installed AND a GPU/DRM card exists
    if command -v cage >/dev/null 2>&1 && command -v chromium >/dev/null 2>&1; then
        if ls /dev/dri/card* >/dev/null 2>&1; then
            echo "kiosk"
            return
        fi
    fi

    # Check for framebuffer or connected display
    if [[ -e /dev/fb0 ]]; then
        # Still need cage for browser rendering
        if command -v cage >/dev/null 2>&1 && command -v chromium >/dev/null 2>&1; then
            echo "kiosk"
            return
        fi
    fi

    # Terminal/TTY available
    echo "tty"
}

# ── Launch ───────────────────────────────────────────────────────────────
main() {
    mkdir -p "$(dirname "$LOG")"
    log "SelenaCore display launcher starting..."

    # Ensure XDG_RUNTIME_DIR exists (not created automatically for root without login session)
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/0}"
    if [[ ! -d "$XDG_RUNTIME_DIR" ]]; then
        mkdir -p "$XDG_RUNTIME_DIR"
        chmod 0700 "$XDG_RUNTIME_DIR"
        log "Created XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
    fi

    wait_for_core || true

    local mode
    mode="$(detect_mode)"
    log "Detected display mode: $mode"

    case "$mode" in
        kiosk)
            wait_for_ui

            log "Launching kiosk: cage + chromium → $UI_URL"

            # Chromium flags for embedded kiosk
            local -a CHROMIUM_FLAGS=(
                --kiosk
                --no-sandbox
                --noerrdialogs
                --disable-infobars
                --disable-session-crashed-bubble
                --disable-translate
                --no-first-run
                --disable-features=Translate,TranslateUI,ChromeTranslatePopup
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
                --disable-gpu-sandbox
                --lang=ru
                --disable-popup-blocking
                --disable-prompt-on-repost
                --disable-hang-monitor
            )

            # DRM for display output, libinput for touch/keyboard/mouse
            export WLR_BACKENDS=drm,libinput
            export LIBSEAT_BACKEND=builtin
            # Hide cursor completely using transparent cursor theme
            export WLR_NO_HARDWARE_CURSORS=1
            local SCRIPT_DIR
            SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
            export XCURSOR_THEME=transparent
            export XCURSOR_PATH="${SCRIPT_DIR}/cursors:/usr/share/icons"
            export XCURSOR_SIZE=24

            # Move cursor off-screen after cage starts (background helper)
            (
                sleep 5
                # Move cursor far off-screen (negative = top-left corner beyond display)
                export WAYLAND_DISPLAY="${XDG_RUNTIME_DIR:-/run/user/0}/wayland-0"
                if command -v wtype >/dev/null 2>&1; then
                    wtype -M 0 0 -m 32767 32767 2>/dev/null || true
                fi
            ) &

            exec cage -s -- chromium "${CHROMIUM_FLAGS[@]}" "$UI_URL"
            ;;

        tty|*)
            log "Launching TTY status / QR wizard screen on tty1..."
            local HOST_IP
            HOST_IP=$(hostname -I | awk '{print $1}')
            exec docker compose -f "$COMPOSE_FILE" \
                exec -T -e HOST_IP="$HOST_IP" core \
                python -m system_modules.ui_core.tty_status
            ;;
    esac
}

main "$@"
