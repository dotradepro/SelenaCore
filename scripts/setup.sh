#!/bin/bash
# scripts/setup.sh — SelenaCore First-Run Setup
#
# Полная автоматическая настройка SelenaCore на чистой системе.
# Поддерживает: Jetson Orin, Raspberry Pi 4/5, Ubuntu 22.04+ (ARM64/x86_64)
#
# Использование:
#   sudo bash scripts/setup.sh [--user USERNAME] [--no-kiosk] [--no-docker]
#
# Что делает:
#   1. Устанавливает Docker (если отсутствует)
#   2. Устанавливает cage + chromium-browser + seatd (kiosk-режим)
#   3. Добавляет пользователя в группы: docker, _seatd, video, input, render
#   4. Создаёт /var/log/selena с правильными правами
#   5. Создаёт .env из .env.example (если отсутствует)
#   6. Собирает Docker-образы
#   7. Устанавливает и активирует selena-display.service
#   8. Запускает контейнеры

set -euo pipefail

# ── Цвета ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

log()     { echo -e "${GREEN}[setup]${NC} $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC}  $*"; }
error()   { echo -e "${RED}[error]${NC} $*" >&2; }
section() { echo -e "\n${BLUE}══ $* ══${NC}"; }

# ── Параметры ──────────────────────────────────────────────────────────────
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_KIOSK=true
INSTALL_DOCKER=true
TARGET_USER="${SUDO_USER:-${USER}}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user)      TARGET_USER="$2"; shift 2 ;;
        --no-kiosk)  INSTALL_KIOSK=false; shift ;;
        --no-docker) INSTALL_DOCKER=false; shift ;;
        *) error "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    error "Запустите с sudo: sudo bash scripts/setup.sh"
    exit 1
fi

if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
    error "Не удалось определить пользователя. Используйте: sudo bash scripts/setup.sh --user USERNAME"
    exit 1
fi

log "Целевой пользователь: $TARGET_USER"
log "Директория проекта:   $REPO_DIR"

# ── 1. Docker ──────────────────────────────────────────────────────────────
section "1. Docker"

if $INSTALL_DOCKER && ! command -v docker &>/dev/null; then
    log "Устанавливаю Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
    log "Docker установлен."
else
    log "Docker уже установлен: $(docker --version 2>/dev/null || echo 'ok')"
fi

# ── 2. Системные зависимости ───────────────────────────────────────────────
section "2. Системные зависимости"

PKGS_NEEDED=()
command -v curl      &>/dev/null || PKGS_NEEDED+=(curl)
command -v unclutter &>/dev/null || { $INSTALL_KIOSK && PKGS_NEEDED+=(unclutter); }
command -v cage      &>/dev/null || { $INSTALL_KIOSK && PKGS_NEEDED+=(cage); }

# chromium: snap-обёртка или нативный
if $INSTALL_KIOSK && ! (command -v chromium &>/dev/null || command -v chromium-browser &>/dev/null); then
    PKGS_NEEDED+=(chromium-browser)
fi

if $INSTALL_KIOSK && ! command -v seatd &>/dev/null; then
    PKGS_NEEDED+=(seatd)
fi

if [[ ${#PKGS_NEEDED[@]} -gt 0 ]]; then
    log "Устанавливаю: ${PKGS_NEEDED[*]}"
    apt-get update -qq
    apt-get install -y --no-install-recommends "${PKGS_NEEDED[@]}"
else
    log "Все зависимости уже установлены."
fi

# ── 3. seatd ──────────────────────────────────────────────────────────────
if $INSTALL_KIOSK; then
    section "3. seatd (seat-менеджер)"
    systemctl enable --now seatd || warn "seatd не удалось включить (возможно, не поддерживается)"
fi

# ── 4. Группы пользователя ────────────────────────────────────────────────
section "4. Группы пользователя"

GROUPS_TO_ADD=(docker)
$INSTALL_KIOSK && GROUPS_TO_ADD+=(video input render)
# _seatd только если группа существует
getent group _seatd &>/dev/null && GROUPS_TO_ADD+=(_seatd)

for grp in "${GROUPS_TO_ADD[@]}"; do
    if id -nG "$TARGET_USER" | grep -qw "$grp"; then
        log "Группа $grp: уже есть"
    else
        usermod -aG "$grp" "$TARGET_USER"
        log "Группа $grp: добавлена"
    fi
done

# ── 5. Директории логов ────────────────────────────────────────────────────
section "5. Директории"

mkdir -p /var/log/selena
chown "$TARGET_USER:$TARGET_USER" /var/log/selena
log "/var/log/selena — OK"

# ── 6. .env ────────────────────────────────────────────────────────────────
section "6. Конфигурация (.env)"

if [[ ! -f "$REPO_DIR/.env" ]]; then
    if [[ -f "$REPO_DIR/.env.example" ]]; then
        cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
        chown "$TARGET_USER:$TARGET_USER" "$REPO_DIR/.env"
        warn ".env создан из .env.example — отредактируйте при необходимости:"
        warn "  nano $REPO_DIR/.env"
    else
        warn ".env.example не найден, .env не создан"
    fi
else
    log ".env уже существует"
fi

# ── 7. Docker-образы ──────────────────────────────────────────────────────
section "7. Сборка Docker-образов"

cd "$REPO_DIR"
log "Запускаю docker compose build..."
sudo -u "$TARGET_USER" docker compose build 2>&1 | tail -5
log "Сборка завершена."

# ── 8. systemd-сервис дисплея ─────────────────────────────────────────────
if $INSTALL_KIOSK; then
    section "8. Сервис дисплея (selena-display.service)"

    SERVICE_DST="/etc/systemd/system/selena-display.service"
    TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)

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
    systemctl daemon-reload
    systemctl enable selena-display.service
    log "selena-display.service установлен и включён."
fi

# ── 9. Запуск контейнеров ─────────────────────────────────────────────────
section "9. Запуск контейнеров"

cd "$REPO_DIR"
log "Запускаю docker compose up -d..."
sudo -u "$TARGET_USER" docker compose up -d 2>&1

sleep 3
log "Статус контейнеров:"
sudo -u "$TARGET_USER" docker compose ps

# ── Итог ──────────────────────────────────────────────────────────────────
section "Готово!"

echo ""
echo -e "${GREEN}SelenaCore успешно настроен!${NC}"
echo ""
echo "  Core API:     http://localhost/api/v1/health"
echo "  UI (HTTP):    http://localhost"
echo "  UI (HTTPS):   https://localhost"
echo ""
if $INSTALL_KIOSK; then
    echo -e "${YELLOW}ВАЖНО: Выйдите и войдите снова, чтобы применить изменения групп:${NC}"
    echo "  Новые группы: ${GROUPS_TO_ADD[*]}"
    echo ""
    echo "  После перелогина kiosk запустится автоматически."
    echo "  Или вручную: sudo systemctl start selena-display.service"
fi
echo ""
echo "  Логи:  journalctl -u selena-display.service -f"
echo "  Стоп:  sudo systemctl stop selena-display.service"
echo "         cd $REPO_DIR && docker compose down"
echo ""
