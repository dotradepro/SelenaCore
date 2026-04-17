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
#   2. systemctl daemon-reload && enable --now <units>
#
# Piper TTS no longer has its own host systemd unit — it runs as a
# subprocess of smarthome-core (see scripts/start.sh). Upgrading from
# a release that had piper-tts.service? Run scripts/migrate_piper_to_container.sh.
#
# Idempotent — running it twice will not error.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "[!] install-systemd.sh must run as root" >&2
    exit 1
fi

# Non-systemd guard: tolerate Alpine/OpenRC and similar by no-oping cleanly
# instead of erroring out. The wizard surfaces this in the task log so users
# know native services won't be installed and points at docs/deploy-native.md.
if ! command -v systemctl >/dev/null 2>&1 || [ ! -d /etc/systemd/system ]; then
    echo "[ ] systemctl not available — skipping native service install."
    echo "    This host is not systemd-based. See docs/deploy-native.md"
    echo "    for manual instructions on OpenRC, runit, or other init systems."
    exit 0
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
for unit in smarthome-core.service smarthome-agent.service selena-display.service; do
    if [ -f "$SYSTEMD_DIR/$unit" ]; then
        if systemctl enable --now "$unit" 2>/dev/null; then
            echo "[+] enabled $unit"
        else
            echo "[!] could not enable $unit (non-fatal)"
        fi
    fi
done

echo "[+] systemd units installed"
