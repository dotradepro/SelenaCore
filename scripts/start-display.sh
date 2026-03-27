#!/bin/bash
# scripts/start-display.sh — Host-side display launcher for SelenaCore
#
# Runs on the HOST (not inside Docker).
# Detects display mode and launches the appropriate UI:
#   desktop     — existing DE session (GNOME/KDE/XFCE) — Chromium kiosk via X11/Wayland
#   kiosk       — cage + Chromium in kiosk mode (Wayland, no DE needed)
#   tty         — Python TUI with QR code + split-panel status on TTY1
#
# Called by smarthome-display.service or ~/.config/autostart after Docker is up.

set -euo pipefail

UI_URL="${SELENA_UI_URL:-http://localhost}"
COMPOSE_FILE="${COMPOSE_FILE:-/home/dotradepro/Downloads/SelenaCore/docker-compose.yml}"
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

# ── Find chromium binary ─────────────────────────────────────────────────
find_chromium() {
    command -v chromium 2>/dev/null \
        || command -v chromium-browser 2>/dev/null \
        || { log "ERROR: chromium not found"; return 1; }
}

# ── Display mode detection ───────────────────────────────────────────────
detect_mode() {
    # 1. Existing DE session (X11 or Wayland) — use it directly
    if [[ -n "${DISPLAY:-}" ]] || [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
        echo "desktop"
        return
    fi

    # 2. No DE — try cage (Wayland compositor, no DE needed)
    if command -v cage >/dev/null 2>&1 && find_chromium >/dev/null 2>&1; then
        if ls /dev/dri/card* >/dev/null 2>&1; then
            echo "kiosk"
            return
        fi
    fi

    # Check for framebuffer
    if [[ -e /dev/fb0 ]]; then
        if command -v cage >/dev/null 2>&1 && find_chromium >/dev/null 2>&1; then
            echo "kiosk"
            return
        fi
    fi

    # 3. Fallback — TTY Python TUI
    echo "tty"
}

# ── Launch ───────────────────────────────────────────────────────────────
main() {
    mkdir -p "$(dirname "$LOG")"
    log "SelenaCore display launcher starting..."

    # Ensure XDG_RUNTIME_DIR exists
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
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
        desktop)
            wait_for_ui

            local CHROMIUM_BIN
            CHROMIUM_BIN="$(find_chromium)"
            log "Launching kiosk via desktop session ($CHROMIUM_BIN) → $UI_URL"

            exec "$CHROMIUM_BIN" \
                --kiosk \
                --no-sandbox \
                --noerrdialogs \
                --disable-infobars \
                --disable-session-crashed-bubble \
                --disable-translate \
                --no-first-run \
                --disable-features=Translate,TranslateUI,ChromeTranslatePopup \
                --check-for-update-interval=31536000 \
                --autoplay-policy=no-user-gesture-required \
                --disable-pinch \
                --overscroll-history-navigation=0 \
                --no-default-browser-check \
                --disable-component-update \
                --disable-background-networking \
                --disable-sync \
                --start-fullscreen \
                --user-data-dir=/tmp/chromium-kiosk \
                --lang=ru \
                --disable-popup-blocking \
                --disable-prompt-on-repost \
                --disable-hang-monitor \
                "${UI_URL}?kiosk=1"
            ;;

        kiosk)
            wait_for_ui

            local CHROMIUM_BIN
            CHROMIUM_BIN="$(find_chromium)"
            log "Launching kiosk: cage + $CHROMIUM_BIN → $UI_URL"

            # Chromium flags for embedded kiosk (Wayland/cage)
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
            export WLR_BACKENDS="${WLR_BACKENDS:-drm,libinput}"
            export LIBSEAT_BACKEND="${LIBSEAT_BACKEND:-seatd}"
            export WLR_NO_HARDWARE_CURSORS="${WLR_NO_HARDWARE_CURSORS:-1}"
            local SCRIPT_DIR
            SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
            export XCURSOR_THEME=transparent
            export XCURSOR_PATH="${SCRIPT_DIR}/cursors:/usr/share/icons"
            export XCURSOR_SIZE=24

            exec cage -s -- "$CHROMIUM_BIN" "${CHROMIUM_FLAGS[@]}" "${UI_URL}?kiosk=1"
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
