#!/usr/bin/env bash
# scripts/install-systemd.sh — install and enable SelenaCore systemd units.
#
# Invoked by the wizard's "install_native_services" provisioning task and
# (optionally) directly from install.sh / by an operator after first boot.
#
# What it does:
#   1. Copies smarthome-core.service, smarthome-agent.service and (if HDMI
#      detected and the user chose kiosk mode) selena-display.service into
#      /etc/systemd/system/.
#   2. Optionally installs scripts/piper-tts.service if Piper GPU mode is
#      configured in core.yaml.
#   3. systemctl daemon-reload && enable --now <units>
#
# Idempotent — running it twice will not error.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "[!] install-systemd.sh must run as root" >&2
    exit 1
fi

REPO_DIR="${SELENA_INSTALL_DIR:-/opt/selena-core}"
SYSTEMD_DIR="/etc/systemd/system"

if [ ! -d "$REPO_DIR" ]; then
    echo "[!] $REPO_DIR not found — run install.sh first" >&2
    exit 1
fi

install_unit() {
    local src="$1"
    local name
    name=$(basename "$src")
    if [ ! -f "$src" ]; then
        echo "[ ] $name not present in repo, skipping"
        return
    fi
    cp "$src" "$SYSTEMD_DIR/$name"
    echo "[+] installed $name"
}

# Core units (always)
install_unit "$REPO_DIR/smarthome-core.service"
install_unit "$REPO_DIR/smarthome-agent.service"

# Piper GPU server (only if user picked GPU TTS in the wizard).
# The service file uses __USER__ / __HOME__ / __SELENA_DIR__ / __PYTHON__
# placeholders that must be substituted before install.
if grep -qE 'cuda:\s*true' "$REPO_DIR/config/core.yaml" 2>/dev/null; then
    if [ -f "$REPO_DIR/scripts/piper-tts.service" ]; then
        PIPER_USER="${SELENA_PIPER_USER:-${SUDO_USER:-root}}"
        PIPER_HOME="$(getent passwd "$PIPER_USER" | cut -d: -f6)"
        [ -z "$PIPER_HOME" ] && PIPER_HOME="/root"
        # Detect which python interpreter has piper installed (install.sh
        # may have used python3.10 from deadsnakes on Focal). Honour the
        # PIPER_PYTHON env var written into .env by install.sh.
        PIPER_PY="${PIPER_PYTHON:-}"
        if [ -z "$PIPER_PY" ] && [ -f "$REPO_DIR/.env" ]; then
            PIPER_PY="$(grep -E '^PIPER_PYTHON=' "$REPO_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")"
        fi
        if [ -z "$PIPER_PY" ]; then
            for cand in python3.11 python3.10 python3.12 python3; do
                if su -s /bin/bash - "$PIPER_USER" -c "$cand -c 'import piper, aiohttp' 2>/dev/null"; then
                    PIPER_PY="$cand"
                    break
                fi
            done
        fi
        [ -z "$PIPER_PY" ] && PIPER_PY="/usr/bin/python3"
        # Resolve to absolute path so systemd ExecStart= is happy.
        if ! echo "$PIPER_PY" | grep -q '^/'; then
            PIPER_PY_ABS="$(command -v "$PIPER_PY" 2>/dev/null || echo "$PIPER_PY")"
            PIPER_PY="$PIPER_PY_ABS"
        fi

        # Ensure user has piper-tts + aiohttp installed under that interpreter
        if ! su -s /bin/bash - "$PIPER_USER" -c "$PIPER_PY -c 'import piper, aiohttp'" >/dev/null 2>&1; then
            su -s /bin/bash - "$PIPER_USER" -c "$PIPER_PY -m pip install --user piper-tts aiohttp" || \
                echo "[!] failed to install piper-tts/aiohttp for $PIPER_USER (non-fatal)"
        fi
        install -d -o "$PIPER_USER" -g "$PIPER_USER" "$PIPER_HOME/.local/share/piper/models"
        sed \
            -e "s|__USER__|$PIPER_USER|g" \
            -e "s|__HOME__|$PIPER_HOME|g" \
            -e "s|__SELENA_DIR__|$REPO_DIR|g" \
            -e "s|__PYTHON__|$PIPER_PY|g" \
            "$REPO_DIR/scripts/piper-tts.service" \
            > "$SYSTEMD_DIR/piper-tts.service"
        echo "[+] installed piper-tts.service (user=$PIPER_USER, python=$PIPER_PY)"
    fi
fi

# Kiosk display (only if there is an HDMI/connected display and `cage` exists)
if command -v cage >/dev/null 2>&1 && [ -d /sys/class/drm ] && \
   ls /sys/class/drm/*/status 2>/dev/null | xargs -r grep -l '^connected' >/dev/null; then
    if [ -f "$REPO_DIR/scripts/start-display.sh" ]; then
        chmod +x "$REPO_DIR/scripts/start-display.sh"
        TARGET_USER="${SELENA_DISPLAY_USER:-${SUDO_USER:-root}}"
        TARGET_UID="$(id -u "$TARGET_USER" 2>/dev/null || echo 0)"
        cat > "$SYSTEMD_DIR/selena-display.service" <<EOF
[Unit]
Description=SelenaCore Kiosk Display
After=network.target docker.service seatd.service
Requires=docker.service
Wants=seatd.service

[Service]
Type=simple
User=$TARGET_USER
ExecStart=$REPO_DIR/scripts/start-display.sh
Restart=on-failure
RestartSec=5
TimeoutStartSec=120
Environment=COMPOSE_FILE=$REPO_DIR/docker-compose.yml
Environment=SELENA_UI_URL=http://localhost
Environment=SELENA_LOG_DIR=/var/log/selena
Environment=WLR_BACKENDS=drm,libinput
Environment=WLR_NO_HARDWARE_CURSORS=1
Environment=LIBSEAT_BACKEND=seatd
Environment=XDG_RUNTIME_DIR=/run/user/$TARGET_UID
StandardOutput=journal
StandardError=journal
SyslogIdentifier=selena-display

[Install]
WantedBy=multi-user.target
EOF
        echo "[+] installed selena-display.service (user=$TARGET_USER)"
    fi
fi

systemctl daemon-reload

# Enable + start core units. Failures non-fatal — the wizard's calling
# task logs them but does not block dashboard reveal.
for unit in smarthome-core.service smarthome-agent.service piper-tts.service selena-display.service; do
    if [ -f "$SYSTEMD_DIR/$unit" ]; then
        if systemctl enable --now "$unit" 2>/dev/null; then
            echo "[+] enabled $unit"
        else
            echo "[!] could not enable $unit (non-fatal)"
        fi
    fi
done

echo "[+] systemd units installed"
