#!/usr/bin/env bash
# SelenaCore — unified bootstrap installer.
#
# Single entry point for fresh devices. After this script finishes you
# get a URL to a browser-based wizard that completes the installation
# (model downloads, voices, LLM, admin user, platform registration).
#
# Usage:
#   git clone https://github.com/dotradepro/SelenaCore.git
#   cd SelenaCore
#   sudo ./install.sh
#
# Optional flags:
#   --no-build         Skip the frontend (Vite) build
#   --no-docker        Don't start docker compose (assume host already manages it)
#   --skip-deps        Skip apt-get package installation
#   --profile NAME     Force a hardware profile (jetson|raspberry|linux_cuda|linux_cpu|minimal)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${SELENA_INSTALL_DIR:-/opt/selena-core}"
DATA_DIR="${SELENA_DATA_DIR:-/var/lib/selena}"
LOG_DIR="${SELENA_LOG_DIR:-/var/log/selena}"
SECURE_DIR="${SELENA_SECURE_DIR:-/secure}"
SELENA_USER="${SELENA_USER:-selena}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log()   { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
err()   { echo -e "${RED}[x]${NC} $*" >&2; }
title() { echo -e "${BOLD}${BLUE}== $* ==${NC}"; }

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        err "This installer must be run as root: sudo ./install.sh"
        exit 1
    fi
}

# ── Hardware Detection (kept for profile-aware messaging) ──────────

detect_hardware() {
    HW_ARCH=$(uname -m)
    HW_RAM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
    HW_RAM_GB=$(( HW_RAM_MB / 1024 ))
    HW_JETSON=false
    HW_RASPBERRY=false
    HW_CUDA=false
    HW_MODEL=""
    HW_PROFILE="linux_cpu"

    if [ -f /proc/device-tree/model ]; then
        HW_MODEL=$(tr -d '\0' < /proc/device-tree/model)
        if echo "$HW_MODEL" | grep -qi "jetson"; then
            HW_JETSON=true
        elif echo "$HW_MODEL" | grep -qi "raspberry"; then
            HW_RASPBERRY=true
        fi
    fi

    if command -v nvidia-smi &>/dev/null || [ -d /usr/local/cuda ]; then
        HW_CUDA=true
    fi

    if $HW_JETSON; then
        HW_PROFILE="jetson"
    elif $HW_RASPBERRY; then
        HW_PROFILE="raspberry"
    elif $HW_CUDA; then
        HW_PROFILE="linux_cuda"
    fi

    log "Hardware detected:"
    echo "    Architecture: $HW_ARCH"
    echo "    RAM:          ${HW_RAM_GB} GB"
    echo "    Device:       ${HW_MODEL:-Unknown}"
    echo "    CUDA:         $HW_CUDA"
    echo "    Profile:      $HW_PROFILE"
}

# ── Host packages ──────────────────────────────────────────────────

install_host_packages() {
    title "Installing host packages"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq

    local packages=(
        ca-certificates curl wget git jq unzip
        python3 python3-venv python3-pip
        ffmpeg libsndfile1
        arp-scan arping
        pulseaudio-utils alsa-utils
        network-manager
        sqlite3
        build-essential
    )

    # Optional kiosk helpers
    apt-get install -y -qq cage wtype >/dev/null 2>&1 || true

    apt-get install -y -qq "${packages[@]}" >/dev/null
    log "Base packages installed"

    # Docker (official convenience script if not present)
    if ! command -v docker &>/dev/null; then
        log "Installing Docker engine via get.docker.com"
        curl -fsSL https://get.docker.com | sh >/dev/null
    else
        log "Docker already installed: $(docker --version)"
    fi

    # docker compose plugin (newer Docker installs include it)
    if ! docker compose version &>/dev/null; then
        warn "docker compose plugin not found — installing"
        apt-get install -y -qq docker-compose-plugin >/dev/null || \
            warn "docker-compose-plugin install failed; install manually"
    fi

    # Node.js (only needed if we will run vite build)
    if ! command -v node &>/dev/null; then
        log "Installing Node.js LTS"
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1 || \
            warn "NodeSource setup failed; will skip Vite build"
        apt-get install -y -qq nodejs >/dev/null 2>&1 || true
    fi
}

# ── User and directories ───────────────────────────────────────────

create_user_and_dirs() {
    title "Creating selena user + directory layout"
    if ! id "$SELENA_USER" &>/dev/null; then
        useradd --system --create-home --shell /usr/sbin/nologin \
            --home-dir "/var/lib/$SELENA_USER" "$SELENA_USER"
        log "Created system user '$SELENA_USER'"
    fi

    # Add the selena user to docker + audio so it can speak to host services
    for grp in docker audio video render bluetooth; do
        getent group "$grp" >/dev/null 2>&1 && usermod -aG "$grp" "$SELENA_USER" || true
    done

    install -d -m 0755 -o "$SELENA_USER" -g "$SELENA_USER" \
        "$DATA_DIR" \
        "$DATA_DIR/models" \
        "$DATA_DIR/models/piper" \
        "$DATA_DIR/models/vosk" \
        "$DATA_DIR/models/whisper" \
        "$DATA_DIR/speaker_embeddings" \
        "$LOG_DIR"

    install -d -m 0750 -o "$SELENA_USER" -g "$SELENA_USER" "$SECURE_DIR"

    # Seed Piper voices from common host caches so the wizard picker shows
    # them as already-installed (avoids re-downloading 60-80MB models).
    seed_piper_voices_from_host
}

seed_piper_voices_from_host() {
    local dest="$DATA_DIR/models/piper"
    local copied=0
    local origin_user="${SUDO_USER:-}"
    local candidates=()
    if [ -n "$origin_user" ] && [ -d "/home/$origin_user/.local/share/piper/models" ]; then
        candidates+=("/home/$origin_user/.local/share/piper/models")
    fi
    [ -d "/root/.local/share/piper/models" ] && candidates+=("/root/.local/share/piper/models")
    [ -d "/usr/local/share/piper" ] && candidates+=("/usr/local/share/piper")

    for src in "${candidates[@]}"; do
        for f in "$src"/*.onnx "$src"/*.onnx.json; do
            [ -f "$f" ] || continue
            local base
            base=$(basename "$f")
            if [ ! -f "$dest/$base" ]; then
                cp "$f" "$dest/$base"
                copied=$((copied + 1))
            fi
        done
    done
    if [ "$copied" -gt 0 ]; then
        chown -R "$SELENA_USER:$SELENA_USER" "$dest"
        log "Seeded $copied Piper voice file(s) from host cache → $dest"
    fi
}

# ── Repo materialization (/opt/selena-core) ────────────────────────

install_repo() {
    title "Materializing /opt/selena-core"
    install -d -m 0755 "$INSTALL_DIR"
    if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
        # Use rsync if available — faster + preserves perms; fall back to cp
        if command -v rsync &>/dev/null; then
            rsync -a --delete \
                --exclude='.git' --exclude='node_modules' --exclude='.venv' \
                "$SCRIPT_DIR/" "$INSTALL_DIR/"
        else
            cp -r "$SCRIPT_DIR"/. "$INSTALL_DIR/"
        fi
        log "Repo synced to $INSTALL_DIR"
    else
        log "Already running from $INSTALL_DIR"
    fi
    chown -R "$SELENA_USER:$SELENA_USER" "$INSTALL_DIR"
}

# ── Config bootstrap ───────────────────────────────────────────────

bootstrap_config() {
    title "Bootstrapping configuration"
    install -d -m 0755 -o "$SELENA_USER" -g "$SELENA_USER" "$INSTALL_DIR/config"

    if [ ! -f "$INSTALL_DIR/config/core.yaml" ] && [ -f "$INSTALL_DIR/config/core.yaml.example" ]; then
        cp "$INSTALL_DIR/config/core.yaml.example" "$INSTALL_DIR/config/core.yaml"
        chown "$SELENA_USER:$SELENA_USER" "$INSTALL_DIR/config/core.yaml"
        log "Created config/core.yaml from example"
    fi

    if [ ! -f "$INSTALL_DIR/.env" ] && [ -f "$INSTALL_DIR/.env.example" ]; then
        cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
        chown "$SELENA_USER:$SELENA_USER" "$INSTALL_DIR/.env"
        log "Created .env from example"
    fi

    # Force first-run flags
    python3 - <<PYEOF
import yaml, pathlib
p = pathlib.Path("$INSTALL_DIR/config/core.yaml")
cfg = yaml.safe_load(p.read_text()) or {}
cfg.setdefault("wizard", {})["completed"] = False
cfg.setdefault("system", {})["initialized"] = False
p.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True))
print("[+] wizard.completed=False persisted")
PYEOF
}

# ── Frontend build ─────────────────────────────────────────────────

build_frontend() {
    title "Building frontend (vite)"
    if ! command -v npm &>/dev/null; then
        warn "npm not available — using pre-built static files (if any)"
        return
    fi
    cd "$INSTALL_DIR"
    if [ ! -d node_modules ]; then
        npm install --silent || warn "npm install reported issues"
    fi
    npx vite build || warn "vite build failed; UI may be stale"
    cd "$SCRIPT_DIR"
}

# ── Docker compose ─────────────────────────────────────────────────

start_docker_stack() {
    title "Starting docker compose stack"
    cd "$INSTALL_DIR"
    docker compose up -d --build
    cd "$SCRIPT_DIR"

    log "Waiting for core to become healthy"
    local tries=0
    until curl -fsS "http://localhost/api/v1/health" >/dev/null 2>&1; do
        tries=$((tries + 1))
        if [ "$tries" -gt 60 ]; then
            warn "Core did not become healthy in 60s — check 'docker compose logs core'"
            return
        fi
        sleep 1
    done
    log "Core is healthy"
}

# ── Systemd unit staging (NOT enabled) ─────────────────────────────

stage_systemd_units() {
    title "Staging systemd units (not enabled)"
    if [ ! -d /etc/systemd/system ]; then
        warn "/etc/systemd/system not present — skipping"
        return
    fi
    for unit in smarthome-core.service smarthome-agent.service scripts/piper-tts.service; do
        if [ -f "$INSTALL_DIR/$unit" ]; then
            cp "$INSTALL_DIR/$unit" "/etc/systemd/system/$(basename "$unit")"
            log "Staged $(basename "$unit")"
        fi
    done
    systemctl daemon-reload || true
    log "Units staged. The wizard's 'install_native_services' step will enable them."
}

# ── Banner ─────────────────────────────────────────────────────────

print_banner() {
    local ip
    ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "<lan-ip>")
    echo ""
    echo -e "${BOLD}${GREEN}"
    echo "  ┌────────────────────────────────────────────────────────┐"
    echo "  │                                                        │"
    echo "  │    SelenaCore is up.  Open the browser wizard:         │"
    echo "  │                                                        │"
    printf "  │      http://%-44s│\n" "${ip}/"
    echo "  │                                                        │"
    echo "  │    The wizard will:                                    │"
    echo "  │     • let you pick STT / TTS / LLM models              │"
    echo "  │     • download them with progress                      │"
    echo "  │     • create the admin user                            │"
    echo "  │     • register the device with the platform            │"
    echo "  │     • install the native systemd services              │"
    echo "  │                                                        │"
    echo "  └────────────────────────────────────────────────────────┘"
    echo -e "${NC}"
    echo "  Logs:"
    echo "    docker compose logs -f core"
    echo "    docker compose logs -f agent"
    echo ""
}

# ── Main ───────────────────────────────────────────────────────────

main() {
    local skip_build=false
    local skip_docker=false
    local skip_deps=false
    local forced_profile=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --no-build)   skip_build=true; shift ;;
            --no-docker)  skip_docker=true; shift ;;
            --skip-deps)  skip_deps=true; shift ;;
            --profile)    forced_profile="$2"; shift 2 ;;
            -h|--help)
                grep -E '^# ' "$0" | sed 's/^# //'
                exit 0 ;;
            *) err "Unknown option: $1"; exit 1 ;;
        esac
    done

    title "SelenaCore unified installer"
    require_root
    detect_hardware
    [ -n "$forced_profile" ] && HW_PROFILE="$forced_profile"

    [ "$skip_deps" = true ] || install_host_packages
    create_user_and_dirs
    install_repo
    bootstrap_config
    [ "$skip_build" = true ] || build_frontend
    [ "$skip_docker" = true ] || start_docker_stack
    stage_systemd_units
    print_banner
}

main "$@"
