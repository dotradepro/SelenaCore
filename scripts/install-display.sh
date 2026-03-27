#!/bin/bash
# scripts/install-display.sh — Установка сервиса дисплея SelenaCore
#
# Запуск (с sudo):
#   sudo bash scripts/install-display.sh [--user USERNAME]
#
# Устанавливает selena-display.service в systemd и запускает его.
# Для полной первоначальной настройки используйте setup.sh.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_USER="${SUDO_USER:-${USER}}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user) TARGET_USER="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: запустите с sudo: sudo bash scripts/install-display.sh"
    exit 1
fi

if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
    echo "ERROR: укажите пользователя: sudo bash scripts/install-display.sh --user USERNAME"
    exit 1
fi

echo "[install-display] Пользователь: $TARGET_USER"
echo "[install-display] Проект: $REPO_DIR"

SERVICE_DST="/etc/systemd/system/selena-display.service"

cat > "$SERVICE_DST" <<EOF
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
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u "$TARGET_USER")

StandardOutput=journal
StandardError=journal
SyslogIdentifier=selena-display

[Install]
WantedBy=multi-user.target
EOF

chmod +x "$REPO_DIR/scripts/start-display.sh"

# Убедиться, что /var/log/selena существует и доступен пользователю
mkdir -p /var/log/selena
chown "$TARGET_USER:$TARGET_USER" /var/log/selena

systemctl daemon-reload
systemctl enable selena-display.service

echo "[install-display] Сервис установлен. Запуск..."
systemctl restart selena-display.service

sleep 3
echo "[install-display] Статус:"
systemctl status selena-display.service --no-pager -l || true
