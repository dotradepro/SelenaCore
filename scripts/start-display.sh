#!/bin/bash
# scripts/start-display.sh — Host-side display launcher for SelenaCore
#
# Runs on the Raspberry Pi HOST (not inside Docker).
# Detects display mode and launches the appropriate UI:
#   kiosk       — Chromium in kiosk mode (X11/Wayland available)
#   framebuffer — Chromium on framebuffer (no X, but /dev/fb0 exists)
#   tty         — Python TUI with QR code on TTY1
#   headless    — nothing (connect via browser over network)
#
# Called by smarthome-display.service after Docker is up and healthy.

set -euo pipefail

UI_URL="${SELENA_UI_URL:-http://localhost:8080}"
SELENA_DIR="${SELENA_DIR:-/opt/selena-core}"
LOG="${SELENA_LOG_DIR:-/var/log/selena}/display.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# ── Wait for UI to be reachable ─────────────────────────────────────────────
wait_for_ui() {
    local tries=0
    log "Waiting for UI at $UI_URL ..."
    until curl -sf "$UI_URL" >/dev/null 2>&1; do
        tries=$((tries + 1))
        if [[ $tries -ge 30 ]]; then
            log "ERROR: UI not reachable after 30 attempts. Giving up."
            exit 1
        fi
        sleep 2
    done
    log "UI is up."
}

# ── Display mode detection ───────────────────────────────────────────────────
detect_mode() {
    # X11 / Wayland available → kiosk
    if [[ -n "${DISPLAY:-}" ]] || [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
        echo "kiosk"
        return
    fi

    # Framebuffer exists
    if [[ -e /dev/fb0 ]]; then
        echo "framebuffer"
        return
    fi

    # HDMI/DRM connected but no X11
    for edid in /sys/class/drm/*/edid; do
        if [[ -f "$edid" ]] && [[ -s "$edid" ]]; then
            echo "framebuffer"
            return
        fi
    done

    # Terminal available
    if [[ -t 1 ]]; then
        echo "tty"
        return
    fi

    echo "headless"
}

# ── Find Chromium binary ─────────────────────────────────────────────────────
find_chromium() {
    for bin in chromium-browser chromium google-chrome; do
        if command -v "$bin" &>/dev/null; then
            echo "$bin"
            return
        fi
    done
    echo ""
}

# ── Launch ───────────────────────────────────────────────────────────────────
main() {
    mkdir -p "$(dirname "$LOG")"
    log "SelenaCore display launcher starting..."

    wait_for_ui

    local mode
    mode="$(detect_mode)"
    log "Detected display mode: $mode"

    case "$mode" in
        kiosk)
            local chromium
            chromium="$(find_chromium)"
            if [[ -z "$chromium" ]]; then
                log "No Chromium found — falling back to tty mode"
                mode="tty"
            else
                log "Launching kiosk: $chromium --kiosk $UI_URL"
                exec "$chromium" \
                    --kiosk \
                    --no-sandbox \
                    --disable-infobars \
                    --disable-session-crashed-bubble \
                    --disable-restore-session-state \
                    --noerrdialogs \
                    --autoplay-policy=no-user-gesture-required \
                    "$UI_URL"
            fi
            ;;&

        framebuffer)
            local chromium
            chromium="$(find_chromium)"
            if [[ -z "$chromium" ]]; then
                log "No Chromium found — falling back to tty mode"
                mode="tty"
            else
                log "Launching framebuffer: $chromium --ozone-platform=drm $UI_URL"
                exec "$chromium" \
                    --ozone-platform=drm \
                    --kiosk \
                    --no-sandbox \
                    --disable-infobars \
                    --disable-session-crashed-bubble \
                    --disable-restore-session-state \
                    --noerrdialogs \
                    "$UI_URL"
            fi
            ;;&

        tty|*)
            log "Launching TTY status / QR wizard screen..."
            if [[ -d "$SELENA_DIR" ]]; then
                cd "$SELENA_DIR"
                local python="${SELENA_DIR}/.venv/bin/python"
                [[ -x "$python" ]] || python="python3"
                exec "$python" -m system_modules.ui_core.tty_status
            else
                log "WARNING: SELENA_DIR=$SELENA_DIR not found. Install SelenaCore first."
                # Minimal fallback: just show IP + URL
                local ip
                ip="$(hostname -I | awk '{print $1}')"
                while true; do
                    clear
                    echo ""
                    echo "  ╔══════════════════════════════════════════╗"
                    echo "  ║   SelenaCore — первый запуск             ║"
                    echo "  ╚══════════════════════════════════════════╝"
                    echo ""
                    echo "  Откройте в браузере:"
                    echo "  http://${ip}:8080"
                    echo ""
                    sleep 10
                done
            fi
            ;;
    esac
}

main "$@"
