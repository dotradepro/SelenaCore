#!/usr/bin/env bash
# SelenaCore — universal capability-based bootstrap installer.
#
# One command for any Debian/Ubuntu-family device — Raspberry Pi 4/5,
# NVIDIA Jetson Orin, x86 laptop, or headless cloud VM. The script
# auto-detects OS, architecture, GPU, audio, display and bluetooth
# capabilities and installs only what is actually needed for that host.
#
# After it finishes you get a URL to a browser-based wizard that
# completes the installation (model downloads, voices, LLM, admin user,
# platform registration).
#
# Usage:
#   git clone https://github.com/dotradepro/SelenaCore.git
#   cd SelenaCore
#   sudo ./install.sh
#
# Optional flags:
#   --skip-deps        Skip apt-get package installation
#   --no-docker        Don't start docker compose (assume external manager)
#   --build-frontend   Install Node.js and re-run vite build (default: skip,
#                      use the committed bundle in system_modules/ui_core/static)
#   --dry-run          Print the planned actions and exit without changes
#   --no-build         (compatibility alias for --build-frontend off, ignored)
#
# NOTE: deliberately NOT using `set -E`. With -E the ERR trap inherits into
# every function, so commands handled with `|| true` or `if ! ...; then` still
# fire the trap and produce a misleading "Installer aborted" line even though
# the script keeps going. Plain `set -e` only aborts on truly unhandled errors,
# which is what we want.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${SELENA_INSTALL_DIR:-/opt/selena-core}"
DATA_DIR="${SELENA_DATA_DIR:-/var/lib/selena}"
LOG_DIR="${SELENA_LOG_DIR:-/var/log/selena}"
SECURE_DIR="${SELENA_SECURE_DIR:-/secure}"
SELENA_USER="${SELENA_USER:-selena}"
APT_QUIET="-qq"
DRY_RUN=false
BUILD_FRONTEND=false
SKIP_DEPS=false
SKIP_DOCKER=false

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

on_abort() {
    local rc=$?
    err "Installer aborted (exit $rc) at line ${BASH_LINENO[0]}."
    err "  Re-run after fixing: sudo ./install.sh"
    err "  Inspect logs:        docker compose logs core"
}
# Without `set -E` this trap only fires on truly unhandled errors that
# trigger `set -e` to abort the script — no spurious "aborted" lines from
# functions that handle their own errors via `|| true` / if-fi.
trap on_abort ERR

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        err "This installer must be run as root: sudo ./install.sh"
        exit 1
    fi
}

run() {
    # Wrapper that respects --dry-run.
    if $DRY_RUN; then
        echo "    [dry-run] $*"
    else
        "$@"
    fi
}

# Run apt-get update. Errors are not silently swallowed — if the index can't
# be refreshed the user sees the actual apt message so they can fix the
# offending repo. We continue regardless because subsequent installs may
# still succeed against a partially-stale cache.
apt_update() {
    $DRY_RUN && { echo "    [dry-run] apt-get update $APT_QUIET"; return 0; }
    if ! apt-get update $APT_QUIET; then
        warn "apt-get update reported errors above (continuing — see ^^^)"
    fi
    return 0
}

# Install a list of packages. If the batch fails the real apt error is
# already on stderr; we then retry one package at a time so a single
# missing optional doesn't kill everything and we can name the offender.
install_apt() {
    [ $# -eq 0 ] && return 0
    if $DRY_RUN; then
        echo "    [dry-run] apt-get install -y $*"
        return 0
    fi
    if DEBIAN_FRONTEND=noninteractive apt-get install -y $APT_QUIET "$@"; then
        return 0
    fi
    warn "Batch install of [$*] failed — retrying packages individually"
    local pkg ok=0 fail=0
    for pkg in "$@"; do
        if DEBIAN_FRONTEND=noninteractive apt-get install -y $APT_QUIET "$pkg"; then
            ok=$((ok + 1))
        else
            warn "  ✗ $pkg — skipped"
            fail=$((fail + 1))
        fi
    done
    log "  $ok package(s) installed, $fail skipped"
    return 0
}

# ── Phase 1: Environment detection ─────────────────────────────────

detect_environment() {
    title "Detecting environment"

    # OS family
    if [ ! -r /etc/os-release ]; then
        err "/etc/os-release missing — unsupported OS"
        exit 1
    fi
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_LIKE="${ID_LIKE:-$OS_ID}"
    OS_CODENAME="${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}"
    OS_VERSION_ID="${VERSION_ID:-}"

    # Reject non-Debian-family distros up front
    if ! echo "$OS_LIKE $OS_ID" | grep -qiE 'debian|ubuntu'; then
        err "$OS_ID is not in the Debian/Ubuntu family. This installer supports"
        err "Debian, Ubuntu, Raspberry Pi OS, Pop!_OS, Linux Mint, Kali, JetPack."
        err "For other distros, see docs/deployment.md."
        exit 1
    fi

    # Architecture
    ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"

    # Hardware identification
    HW_MODEL=""
    if [ -r /proc/device-tree/model ]; then
        HW_MODEL=$(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo "")
    fi

    IS_RPI=false
    echo "$HW_MODEL" | grep -qi raspberry && IS_RPI=true

    IS_JETSON=false
    [ -f /etc/nv_tegra_release ] && IS_JETSON=true
    echo "$HW_MODEL" | grep -qi jetson && IS_JETSON=true

    IS_VM=false
    if command -v systemd-detect-virt >/dev/null 2>&1; then
        local v
        v=$(systemd-detect-virt 2>/dev/null || echo none)
        [ "$v" != "none" ] && IS_VM=true
    fi

    # GPU detection (CUDA / NVIDIA)
    HAS_NVIDIA=false
    if command -v nvidia-smi >/dev/null 2>&1 \
       || [ -e /dev/nvidia0 ] \
       || [ -d /usr/local/cuda ] \
       || [ -d /usr/lib/aarch64-linux-gnu/tegra ] \
       || $IS_JETSON; then
        HAS_NVIDIA=true
    fi

    # Audio: presence of any ALSA card
    HAS_AUDIO=false
    if ls /proc/asound/card[0-9]* >/dev/null 2>&1; then
        HAS_AUDIO=true
    fi

    # Display: any DRM connector with status "connected"
    HAS_DISPLAY=false
    if ls /sys/class/drm/*/status >/dev/null 2>&1; then
        if grep -lqs '^connected$' /sys/class/drm/*/status 2>/dev/null; then
            HAS_DISPLAY=true
        fi
    fi

    # Bluetooth: hci device or rfkill bluetooth entry
    HAS_BT=false
    if ls /sys/class/bluetooth/hci* >/dev/null 2>&1; then
        HAS_BT=true
    fi

    HEADLESS=false
    if ! $HAS_AUDIO && ! $HAS_DISPLAY; then HEADLESS=true; fi

    # RAM bucket
    local mem_kb
    mem_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo)
    RAM_GB=$(( mem_kb / 1024 / 1024 ))

    # Friendly profile label (informational only)
    if $IS_JETSON; then        PROFILE="jetson"
    elif $IS_RPI; then         PROFILE="raspberry"
    elif $HAS_NVIDIA; then     PROFILE="linux_cuda"
    elif $HEADLESS; then       PROFILE="cloud_headless"
    else                       PROFILE="linux_cpu"
    fi

    log "Environment:"
    echo "    OS:        $OS_ID $OS_VERSION_ID ($OS_CODENAME), like=$OS_LIKE"
    echo "    Arch:      $ARCH"
    echo "    Hardware:  ${HW_MODEL:-generic}"
    echo "    RAM:       ${RAM_GB} GB"
    echo "    Flags:     RPi=$IS_RPI Jetson=$IS_JETSON VM=$IS_VM"
    echo "               NVIDIA=$HAS_NVIDIA Audio=$HAS_AUDIO Display=$HAS_DISPLAY"
    echo "               Bluetooth=$HAS_BT Headless=$HEADLESS"
    echo "    Profile:   $PROFILE"
}

# ── Phase 2: Conditional package install ───────────────────────────

BASE_PACKAGES=(
    ca-certificates curl wget git jq unzip
    python3 python3-venv python3-pip python3-yaml
    ffmpeg libsndfile1
    arp-scan arping iproute2 net-tools
    network-manager
    sqlite3
    build-essential
)

install_host_packages() {
    title "Installing host packages"
    apt_update
    install_apt "${BASE_PACKAGES[@]}"

    if $HAS_AUDIO; then
        log "Audio detected — installing pulseaudio/alsa utilities"
        install_apt pulseaudio-utils alsa-utils
    else
        log "No audio devices — skipping pulseaudio/alsa"
    fi

    if $HAS_BT; then
        log "Bluetooth detected — installing bluez"
        install_apt bluez bluez-tools
    fi

    if $HAS_DISPLAY && ! $HEADLESS; then
        # cage / wtype / seatd are only in jammy/noble/bookworm/trixie main
        # repos. Older releases (focal, bullseye) don't ship them, so we skip
        # quietly instead of producing three "Unable to locate package" errors.
        local kiosk_supported=true
        case "$OS_CODENAME" in
            focal|bullseye|buster|xenial|stretch) kiosk_supported=false ;;
        esac
        if $kiosk_supported; then
            log "Display detected — installing kiosk helpers (cage, wtype, seatd)"
            install_apt cage wtype seatd
        else
            log "Display detected, but $OS_ID $OS_CODENAME does not ship cage/wtype/seatd in main repos — skipping kiosk helpers (selena-display.service won't be installed)"
        fi
    else
        log "No display — skipping kiosk helpers"
    fi
}

# ── Phase 2.5: Native AI runtimes (Ollama + Piper) ─────────────────
# These run on the HOST, not inside the docker container, because the
# wizard cannot install host-level binaries from inside the sandbox.
# install.sh runs them once during bootstrap; the wizard then handles
# model downloads via the already-installed CLIs.

install_native_runtimes() {
    title "Installing native AI runtimes (Ollama + Piper)"
    install_ollama
    install_piper_runtime
}

install_ollama() {
    if command -v ollama >/dev/null 2>&1; then
        log "Ollama already installed: $(ollama --version 2>/dev/null | head -1)"
        run systemctl enable --now ollama 2>/dev/null || true
        return 0
    fi
    if $DRY_RUN; then
        echo "    [dry-run] curl -fsSL https://ollama.com/install.sh | sh"
        return 0
    fi
    log "Installing Ollama via official installer (https://ollama.com/install.sh)"
    if ! curl -fsSL https://ollama.com/install.sh | sh; then
        warn "Ollama install script failed — LLM features will be unavailable until installed manually"
        return 0
    fi
    # The official installer creates ollama.service. Make sure it's enabled.
    systemctl enable --now ollama 2>/dev/null || \
        warn "Could not enable ollama.service — start it manually with: sudo systemctl start ollama"
    log "Ollama installed: $(ollama --version 2>/dev/null | head -1 || echo unknown)"
}

install_piper_runtime() {
    # The Piper Python package + a small runner that the wizard's
    # piper-tts.service expects. Install for the SUDO_USER (the operator),
    # not root, so the systemd unit's User=__USER__ template can find it.
    #
    # Sets the global PIPER_PYTHON to the absolute path of the interpreter
    # that has piper installed. Leaves PIPER_PYTHON UNSET if the runtime
    # could not be installed — the rest of the installer treats an unset
    # PIPER_PYTHON as "no native Piper, skip the systemd unit".
    PIPER_PYTHON=""
    local piper_user="${SUDO_USER:-root}"
    local piper_home
    piper_home="$(getent passwd "$piper_user" | cut -d: -f6 || true)"
    [ -z "$piper_home" ] && piper_home="/root"

    # Resolve a python3.9+ interpreter that can install piper-tts wheels.
    # Strategy (first that works wins):
    #   1. System python3 >= 3.9 (jammy+, bookworm, Pi OS Bookworm, Jetson JetPack 6)
    #   2. deadsnakes apt PPA (Ubuntu jammy+/noble — gives python3.10)
    #   3. uv-managed standalone python3.11 (focal, bullseye, buster, old Pi OS)
    #
    # If none work — log an informative message and continue without native
    # Piper (container TTS still renders the wizard's voice selection step).
    local py=""
    if ! py="$(_ensure_piper_python "$piper_user" "$piper_home")" || [ -z "$py" ]; then
        log "Native Piper TTS not installed on this OS. The wizard's TTS step will still work via the docker container build."
        return 0
    fi

    if su -s /bin/bash - "$piper_user" -c "'$py' -c 'import piper, aiohttp' 2>/dev/null"; then
        log "Piper TTS Python package already installed for $piper_user (via $py)"
        install -d -o "$piper_user" -g "$piper_user" "$piper_home/.local/share/piper/models"
        PIPER_PYTHON="$py"
        return 0
    fi
    if $DRY_RUN; then
        echo "    [dry-run] install piper-tts + aiohttp via $py for $piper_user"
        PIPER_PYTHON="$py"
        return 0
    fi

    log "Installing Piper TTS Python package for user '$piper_user' via $py (~80 MB onnxruntime)"
    local pip_cmd="'$py' -m pip install --user piper-tts aiohttp"
    # --break-system-packages tolerates PEP 668 on noble/bookworm where the
    # system python is externally-managed.
    if ! su -s /bin/bash - "$piper_user" -c "$pip_cmd --break-system-packages 2>&1" >/tmp/_piper_pip.log; then
        if ! su -s /bin/bash - "$piper_user" -c "$pip_cmd 2>&1" >/tmp/_piper_pip.log; then
            warn "pip install piper-tts failed — TTS will be unavailable until installed manually"
            tail -n 10 /tmp/_piper_pip.log >&2
            return 0
        fi
    fi
    install -d -o "$piper_user" -g "$piper_user" "$piper_home/.local/share/piper/models"
    PIPER_PYTHON="$py"
    log "Piper TTS Python package installed for $piper_user (via $PIPER_PYTHON)"
}

# ── Piper Python resolver ─────────────────────────────────────────── #

_ensure_piper_python() {
    # Prints the absolute path of a python3.9+ interpreter usable by
    # SUDO_USER. Returns 1 if none can be installed on this platform.
    #
    # IMPORTANT: this function's stdout is captured by the caller, so every
    # diagnostic must be written to stderr. Only the final interpreter path
    # goes to stdout on the last line.
    local piper_user="$1"
    local piper_home="$2"

    # 1. System python3 >= 3.9
    local sys_minor
    sys_minor="$(python3 -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 0)"
    if [ "$sys_minor" -ge 9 ] 2>/dev/null; then
        command -v python3
        return 0
    fi

    log "System python3 is 3.$sys_minor (too old for piper-tts wheels) — looking for an alternative" >&2

    # 2. deadsnakes apt PPA — only for Ubuntu jammy+ / derivatives
    if _try_deadsnakes_python310 >&2; then
        command -v python3.10
        return 0
    fi

    # 3. uv fallback — works on any modern-glibc Linux amd64/arm64
    local uv_py=""
    if uv_py="$(_try_uv_python "$piper_user" "$piper_home")" && [ -n "$uv_py" ]; then
        printf '%s\n' "$uv_py"
        return 0
    fi

    return 1
}

_try_deadsnakes_python310() {
    # Install python3.10 from deadsnakes PPA on supported Ubuntu releases.
    # Returns 0 if python3.10 is on PATH afterwards, 1 otherwise.
    if command -v python3.10 >/dev/null 2>&1; then
        return 0
    fi

    # Skip releases where deadsnakes no longer publishes packages.
    case "$OS_CODENAME" in
        focal|bionic|xenial|buster|stretch|bullseye)
            log "$OS_ID $OS_CODENAME: deadsnakes does not ship python3.10 here — will try uv instead"
            return 1
            ;;
    esac
    case "$OS_ID" in
        ubuntu|pop|linuxmint) ;;
        *)
            log "$OS_ID: deadsnakes PPA is Ubuntu-only — will try uv instead"
            return 1
            ;;
    esac

    install_apt gnupg curl ca-certificates
    install -d -m 0755 /etc/apt/keyrings
    local key_url="https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xF23C5A6CF475977595C89F51BA6932366A755776"
    if ! curl -fsSL "$key_url" | gpg --dearmor --batch --yes -o /etc/apt/keyrings/deadsnakes.gpg 2>/dev/null; then
        warn "Could not download deadsnakes GPG key"
        return 1
    fi
    chmod 0644 /etc/apt/keyrings/deadsnakes.gpg
    local repo_codename="$OS_CODENAME"
    case "$repo_codename" in
        jammy|noble|oracular|plucky) ;;
        *) repo_codename="jammy" ;;
    esac
    echo "deb [signed-by=/etc/apt/keyrings/deadsnakes.gpg] https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu $repo_codename main" \
        > /etc/apt/sources.list.d/deadsnakes.list
    apt_update
    if ! apt-cache madison python3.10 2>/dev/null | grep -q .; then
        warn "deadsnakes PPA for $repo_codename does not publish python3.10 — skipping"
        rm -f /etc/apt/sources.list.d/deadsnakes.list
        return 1
    fi
    install_apt python3.10 python3.10-venv python3.10-distutils python3.10-dev
    command -v python3.10 >/dev/null 2>&1 || return 1
    # Bootstrap pip for python3.10 if it's missing
    if ! python3.10 -m pip --version >/dev/null 2>&1; then
        curl -fsSL https://bootstrap.pypa.io/get-pip.py | python3.10 || return 1
    fi
    return 0
}

_try_uv_python() {
    # Install uv (a ~15MB single-binary Python package manager from Astral)
    # and use it to download a standalone CPython 3.11 — works on any
    # Linux with modern glibc, independent of distro repos.
    #
    # Prints the absolute path of the uv-managed python3.11 on stdout on
    # success. ALL diagnostics MUST go to stderr because the caller
    # captures our stdout.
    local piper_user="$1"
    local piper_home="$2"

    # uv doesn't publish armv7 or old-glibc builds — bail on unsupported arches.
    case "$ARCH" in
        amd64|arm64) ;;
        *)
            log "uv does not ship a $ARCH binary — cannot install a newer Python automatically" >&2
            return 1
            ;;
    esac

    # 1. Ensure uv is installed (for SUDO_USER, ~/.local/bin/uv)
    local uv_bin="$piper_home/.local/bin/uv"
    if [ ! -x "$uv_bin" ]; then
        if $DRY_RUN; then
            echo "    [dry-run] curl -LsSf https://astral.sh/uv/install.sh | sh  (as $piper_user)" >&2
            printf '%s\n' "$piper_home/.local/share/uv/python/cpython-3.11/bin/python3.11"
            return 0
        fi
        log "Installing uv (~15 MB standalone package manager) for user '$piper_user'" >&2
        if ! su -s /bin/bash - "$piper_user" -c \
                'curl -LsSf https://astral.sh/uv/install.sh | sh' >/tmp/_uv_install.log 2>&1; then
            warn "uv installer script failed — see /tmp/_uv_install.log" >&2
            return 1
        fi
    fi
    if [ ! -x "$uv_bin" ]; then
        warn "uv binary not found at $uv_bin after installer ran" >&2
        return 1
    fi
    log "uv ready: $uv_bin" >&2

    # 2. Install a standalone CPython 3.11
    if ! su -s /bin/bash - "$piper_user" -c "'$uv_bin' python install 3.11" >/tmp/_uv_python.log 2>&1; then
        warn "uv failed to install Python 3.11 — see /tmp/_uv_python.log" >&2
        tail -n 10 /tmp/_uv_python.log >&2
        return 1
    fi

    # 3. Resolve its absolute path. `uv python find 3.11` prints the path.
    local uv_py
    uv_py="$(su -s /bin/bash - "$piper_user" -c "'$uv_bin' python find 3.11" 2>/dev/null | head -1)"
    if [ -z "$uv_py" ] || [ ! -x "$uv_py" ]; then
        warn "uv reported no usable python3.11 at '$uv_py'" >&2
        return 1
    fi

    log "uv-managed Python 3.11 ready: $uv_py" >&2
    printf '%s\n' "$uv_py"
    return 0
}

# ── Phase 3: Robust Docker installation ────────────────────────────

install_docker() {
    title "Installing Docker"

    # 1. Already working?
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        log "Docker + compose plugin already present: $(docker --version)"
        run systemctl enable --now docker 2>/dev/null || true
        if $HAS_NVIDIA && ! $IS_JETSON; then
            install_nvidia_container_toolkit
        fi
        return 0
    fi

    # 2. Jetson — JetPack ships docker pre-configured with nvidia runtime
    if $IS_JETSON; then
        if command -v docker >/dev/null 2>&1; then
            log "Jetson: using JetPack-provided Docker ($(docker --version))"
            run systemctl enable --now docker 2>/dev/null || true
            return 0
        fi
        warn "Jetson without Docker is unusual — proceeding via apt"
    fi

    # 3. Official Docker repo
    local repo_id="$OS_ID"
    case "$OS_ID" in
        raspbian)            repo_id=debian ;;
        pop|linuxmint|kali)  repo_id=ubuntu ;;
    esac
    local key_url="https://download.docker.com/linux/$repo_id/gpg"
    local repo_url="https://download.docker.com/linux/$repo_id"

    log "Installing Docker via official repo ($repo_id $OS_CODENAME)"
    if _install_docker_official "$repo_id" "$key_url" "$repo_url"; then
        log "Docker installed via official repo"
    else
        warn "Official Docker repo failed — falling back to distro packages"
        _install_docker_distro_fallback
    fi

    run systemctl enable --now docker 2>/dev/null || \
        warn "Could not enable docker via systemctl (no systemd?). Start it manually."

    # NVIDIA container toolkit (skip on Jetson, where it's already wired)
    if $HAS_NVIDIA && ! $IS_JETSON; then
        install_nvidia_container_toolkit
    fi

    # Verify
    if ! docker compose version >/dev/null 2>&1 && ! docker-compose version >/dev/null 2>&1; then
        err "Docker compose is not available after install. Aborting."
        exit 1
    fi
}

_install_docker_official() {
    local repo_id="$1" key_url="$2" repo_url="$3"
    install_apt ca-certificates curl gnupg
    run install -m 0755 -d /etc/apt/keyrings
    if ! $DRY_RUN; then
        curl -fsSL "$key_url" -o /etc/apt/keyrings/docker.asc || return 1
        chmod a+r /etc/apt/keyrings/docker.asc
        echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.asc] $repo_url $OS_CODENAME stable" \
            > /etc/apt/sources.list.d/docker.list
    fi
    apt_update
    # Explicit minimal package list — no docker-model-plugin, no rootless-extras
    install_apt docker-ce docker-ce-cli containerd.io \
                docker-buildx-plugin docker-compose-plugin
    command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1
}

_install_docker_distro_fallback() {
    install_apt docker.io
    if ! docker compose version >/dev/null 2>&1; then
        install_apt docker-compose-v2 || true
    fi
    if ! docker compose version >/dev/null 2>&1; then
        install_apt docker-compose-plugin || true
    fi
    if ! docker compose version >/dev/null 2>&1; then
        install_apt docker-compose || true
    fi
}

install_nvidia_container_toolkit() {
    if command -v nvidia-ctk >/dev/null 2>&1; then
        log "nvidia-container-toolkit already installed"
        return 0
    fi
    log "Installing nvidia-container-toolkit"
    if ! $DRY_RUN; then
        local distribution
        distribution="$(. /etc/os-release; echo "${ID}${VERSION_ID}")"
        curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
            | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg 2>/dev/null || true
        curl -fsSL "https://nvidia.github.io/libnvidia-container/${distribution}/libnvidia-container.list" \
            | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
            > /etc/apt/sources.list.d/nvidia-container-toolkit.list 2>/dev/null || \
            warn "NVIDIA toolkit repo not available for $distribution — skipping"
    fi
    apt_update
    install_apt nvidia-container-toolkit || {
        warn "nvidia-container-toolkit install failed — GPU passthrough may not work"
        return 0
    }
    if ! $DRY_RUN; then
        nvidia-ctk runtime configure --runtime=docker 2>/dev/null || true
        systemctl restart docker 2>/dev/null || true
    fi
}

# ── User and directories ───────────────────────────────────────────

create_user_and_dirs() {
    title "Creating selena user + directory layout"
    if ! id "$SELENA_USER" &>/dev/null; then
        run useradd --system --create-home --shell /usr/sbin/nologin \
            --home-dir "/var/lib/$SELENA_USER" "$SELENA_USER"
        log "Created system user '$SELENA_USER'"
    fi

    for grp in docker audio video render bluetooth; do
        getent group "$grp" >/dev/null 2>&1 && run usermod -aG "$grp" "$SELENA_USER" || true
    done

    run install -d -m 0755 -o "$SELENA_USER" -g "$SELENA_USER" \
        "$DATA_DIR" \
        "$DATA_DIR/models" \
        "$DATA_DIR/models/piper" \
        "$DATA_DIR/models/whisper" \
        "$DATA_DIR/speaker_embeddings" \
        "$LOG_DIR"

    run install -d -m 0750 -o "$SELENA_USER" -g "$SELENA_USER" "$SECURE_DIR"

    # Canonical model directories on the HOST (not docker volumes), so the
    # native services (piper-tts.service, ollama.service) and the container
    # both see the same files. The user can later add/remove voices via the
    # Settings UI OR by simply dropping files into these dirs.
    create_shared_model_dirs
    seed_piper_voices_from_host
}

create_shared_model_dirs() {
    local origin_user="${SUDO_USER:-root}"
    local origin_home
    origin_home="$(getent passwd "$origin_user" | cut -d: -f6 || echo /root)"

    # Piper voices live in the operator's home (matches scripts/piper-tts.service
    # which gets templated with __USER__/__HOME__ pointing at SUDO_USER).
    PIPER_HOST_DIR="$origin_home/.local/share/piper/models"
    run install -d -m 0775 -o "$origin_user" -g "$origin_user" "$origin_home/.local/share/piper"
    run install -d -m 0775 -o "$origin_user" -g "$origin_user" "$PIPER_HOST_DIR"

    # Vosk lives under /var/lib/selena/models/vosk, owned by the operator so
    # they can drop/delete models manually. The container (root) has rwx
    # everywhere by virtue of running privileged.
    VOSK_HOST_DIR="$DATA_DIR/models/vosk"
    run install -d -m 0775 -o "$origin_user" -g "$origin_user" "$VOSK_HOST_DIR"

    # Ollama models live where the official installer put them. The wizard's
    # provisioning calls the host ollama.service over HTTP, so we just need
    # to make sure the dir exists; ollama.service writes to it.
    OLLAMA_HOST_DIR="/usr/share/ollama/.ollama/models"
    [ -d "$OLLAMA_HOST_DIR" ] || run install -d -m 0755 "$OLLAMA_HOST_DIR" 2>/dev/null || true

    log "Shared model dirs:"
    echo "    Piper:  $PIPER_HOST_DIR"
    echo "    Vosk:   $VOSK_HOST_DIR"
    echo "    Ollama: $OLLAMA_HOST_DIR"
}

seed_piper_voices_from_host() {
    # Voices already in the operator's piper dir don't need seeding — they're
    # already there. We only copy from /usr/local/share/piper or /root caches
    # if they exist.
    local dest="${PIPER_HOST_DIR:-}"
    [ -z "$dest" ] && return 0
    local origin_user="${SUDO_USER:-root}"
    local copied=0
    local candidates=()
    [ -d "/root/.local/share/piper/models" ] && [ "/root/.local/share/piper/models" != "$dest" ] && candidates+=("/root/.local/share/piper/models")
    [ -d "/usr/local/share/piper" ] && candidates+=("/usr/local/share/piper")

    [ ${#candidates[@]} -eq 0 ] && return 0
    $DRY_RUN && { echo "    [dry-run] would seed Piper voices from ${candidates[*]}"; return; }

    local src f base
    for src in "${candidates[@]}"; do
        for f in "$src"/*.onnx "$src"/*.onnx.json; do
            [ -f "$f" ] || continue
            base=$(basename "$f")
            if [ ! -f "$dest/$base" ]; then
                cp "$f" "$dest/$base"
                copied=$((copied + 1))
            fi
        done
    done
    if [ "$copied" -gt 0 ]; then
        chown -R "$origin_user:$origin_user" "$dest"
        log "Seeded $copied Piper voice file(s) from host cache → $dest"
    fi
}

# ── Repo materialization ───────────────────────────────────────────

install_repo() {
    title "Materializing $INSTALL_DIR"
    run install -d -m 0755 "$INSTALL_DIR"
    if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
        if command -v rsync >/dev/null 2>&1; then
            run rsync -a --delete \
                --exclude='.git' --exclude='node_modules' --exclude='.venv' \
                "$SCRIPT_DIR/" "$INSTALL_DIR/"
        else
            run cp -r "$SCRIPT_DIR"/. "$INSTALL_DIR/"
        fi
        log "Repo synced to $INSTALL_DIR"
    else
        log "Already running from $INSTALL_DIR"
    fi
    run chown -R "$SELENA_USER:$SELENA_USER" "$INSTALL_DIR"
}

# ── Config bootstrap ───────────────────────────────────────────────

bootstrap_config() {
    title "Bootstrapping configuration"
    run install -d -m 0755 -o "$SELENA_USER" -g "$SELENA_USER" "$INSTALL_DIR/config"

    if [ ! -f "$INSTALL_DIR/config/core.yaml" ] && [ -f "$INSTALL_DIR/config/core.yaml.example" ]; then
        run cp "$INSTALL_DIR/config/core.yaml.example" "$INSTALL_DIR/config/core.yaml"
        run chown "$SELENA_USER:$SELENA_USER" "$INSTALL_DIR/config/core.yaml"
        log "Created config/core.yaml from example"
    fi

    if [ ! -f "$INSTALL_DIR/.env" ] && [ -f "$INSTALL_DIR/.env.example" ]; then
        run cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
        run chown "$SELENA_USER:$SELENA_USER" "$INSTALL_DIR/.env"
        log "Created .env from example"
    fi

    # Pin docker-compose mounts to the canonical host paths so the container
    # and the native services share the same model directories.
    write_env_overrides

    if $DRY_RUN; then
        echo "    [dry-run] would force wizard.completed=False / system.initialized=False"
        return
    fi
    python3 - <<PYEOF
import yaml, pathlib
p = pathlib.Path("$INSTALL_DIR/config/core.yaml")
cfg = yaml.safe_load(p.read_text()) or {}
cfg.setdefault("wizard", {})["completed"] = False
cfg.setdefault("system", {})["initialized"] = False
# Make the in-process voice/STT readers point at the SAME directories the
# wizard's provisioning step writes into and the host services consume.
cfg.setdefault("voice", {}).setdefault("tts", {})["models_dir"] = "/var/lib/selena/models/piper"
cfg.setdefault("stt", {}).setdefault("vosk", {})["models_dir"] = "/var/lib/selena/models/vosk"
p.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True))
print("[+] wizard.completed=False, model dirs pinned in core.yaml")
PYEOF
}

write_env_overrides() {
    # Append/update the docker-compose env vars that drive the bind mounts:
    #   PIPER_MODELS_DIR  → mounted at /var/lib/selena/models/piper inside core
    #   VOSK_MODELS_DIR   → mounted at /var/lib/selena/models/vosk inside core
    #   OLLAMA_MODELS_DIR → mounted RO at the same path inside core
    #   HOST_UID          → for the PulseAudio socket path
    local env_file="$INSTALL_DIR/.env"
    [ -f "$env_file" ] || run touch "$env_file"
    local origin_user="${SUDO_USER:-root}"
    local origin_uid
    origin_uid="$(id -u "$origin_user" 2>/dev/null || echo 0)"
    local piper_dir="${PIPER_HOST_DIR:-/root/.local/share/piper/models}"
    local vosk_dir="${VOSK_HOST_DIR:-$DATA_DIR/models/vosk}"
    local ollama_dir="${OLLAMA_HOST_DIR:-/usr/share/ollama/.ollama/models}"

    if $DRY_RUN; then
        echo "    [dry-run] would write PIPER_MODELS_DIR=$piper_dir VOSK_MODELS_DIR=$vosk_dir OLLAMA_MODELS_DIR=$ollama_dir HOST_UID=$origin_uid into $env_file"
        return
    fi
    _set_env_var "$env_file" PIPER_MODELS_DIR  "$piper_dir"
    _set_env_var "$env_file" VOSK_MODELS_DIR   "$vosk_dir"
    _set_env_var "$env_file" OLLAMA_MODELS_DIR "$ollama_dir"
    _set_env_var "$env_file" HOST_UID          "$origin_uid"
    # Record which python interpreter has piper-tts installed (may be a
    # newer one we pulled from deadsnakes when system python was 3.8).
    if [ -n "${PIPER_PYTHON:-}" ]; then
        _set_env_var "$env_file" PIPER_PYTHON "$PIPER_PYTHON"
    fi
    chown "$SELENA_USER:$SELENA_USER" "$env_file" 2>/dev/null || true
    log "Pinned docker-compose env: PIPER/VOSK/OLLAMA dirs + HOST_UID=$origin_uid${PIPER_PYTHON:+ + PIPER_PYTHON=$PIPER_PYTHON}"
}

_set_env_var() {
    local file="$1" key="$2" value="$3"
    if grep -qE "^${key}=" "$file"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
}

# ── Phase 4: Frontend (skipped by default — bundle is committed) ───

build_frontend() {
    # Check both SCRIPT_DIR (the repo we just cloned) and INSTALL_DIR (the
    # rsync target) — in dry-run mode the rsync is a no-op but the bundle
    # is still committed in SCRIPT_DIR.
    local present=false
    [ -f "$SCRIPT_DIR/system_modules/ui_core/static/index.html" ] && present=true
    [ -f "$INSTALL_DIR/system_modules/ui_core/static/index.html" ] && present=true
    if $present && ! $BUILD_FRONTEND; then
        log "Frontend bundle already present (committed). Skipping vite build."
        log "  Pass --build-frontend to force a rebuild."
        return
    fi
    title "Building frontend (vite)"
    if ! command -v node >/dev/null 2>&1; then
        log "Installing Node.js LTS for vite build"
        if ! $DRY_RUN; then
            curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1 || \
                { warn "NodeSource setup failed; cannot build frontend"; return; }
        fi
        install_apt nodejs
    fi
    if ! command -v npm >/dev/null 2>&1; then
        warn "npm still missing — cannot build frontend"
        return
    fi
    cd "$INSTALL_DIR"
    if [ ! -d node_modules ]; then
        run npm install --silent || warn "npm install reported issues"
    fi
    run npx vite build || warn "vite build failed; UI may be stale"
    cd "$SCRIPT_DIR"
}

# ── Phase 5: GPU-aware compose start ───────────────────────────────

start_docker_stack() {
    title "Starting docker compose stack"
    local compose_args=(-f "$INSTALL_DIR/docker-compose.yml")
    if $HAS_NVIDIA && [ -f "$INSTALL_DIR/docker-compose.gpu.yml" ]; then
        compose_args+=(-f "$INSTALL_DIR/docker-compose.gpu.yml")
        log "Using GPU compose override (docker-compose.gpu.yml)"
    fi

    if $DRY_RUN; then
        echo "    [dry-run] cd $INSTALL_DIR && docker compose ${compose_args[*]} up -d --build"
        return
    fi

    cd "$INSTALL_DIR"
    docker compose "${compose_args[@]}" up -d --build
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

# ── Systemd unit staging (NOT enabled — wizard does that) ──────────

stage_systemd_units() {
    title "Staging systemd units (not enabled)"
    if [ ! -d /etc/systemd/system ]; then
        warn "/etc/systemd/system not present — skipping"
        return
    fi
    # Only stage units that are READY-TO-USE as-is. piper-tts.service is
    # NOT staged here — it contains __USER__/__HOME__/__SELENA_DIR__/__PYTHON__
    # placeholders that scripts/install-systemd.sh substitutes when (and
    # only when) the wizard's install_native_services step runs after Piper
    # was successfully installed.
    for unit in smarthome-core.service smarthome-agent.service; do
        if [ -f "$INSTALL_DIR/$unit" ]; then
            run cp "$INSTALL_DIR/$unit" "/etc/systemd/system/$(basename "$unit")"
            log "Staged $(basename "$unit")"
        fi
    done
    # If a stale piper-tts.service from a previous bad run exists, remove it
    # so systemd doesn't keep complaining about __PYTHON__ on every boot.
    if [ -f /etc/systemd/system/piper-tts.service ]; then
        if grep -q '__PYTHON__\|__USER__' /etc/systemd/system/piper-tts.service 2>/dev/null; then
            run rm -f /etc/systemd/system/piper-tts.service
            log "Removed stale templated piper-tts.service from /etc/systemd/system"
        fi
    fi
    run systemctl daemon-reload || true
    log "Units staged. The wizard's 'install_native_services' step will enable them."
}

# ── Phase 7: Banner with environment summary ───────────────────────

print_banner() {
    local ip caps=()
    ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "<lan-ip>")
    [ -z "$ip" ] && ip="<lan-ip>"
    $HAS_NVIDIA && caps+=("NVIDIA")
    $HAS_AUDIO  && caps+=("audio")
    $HAS_DISPLAY && caps+=("display")
    $HAS_BT     && caps+=("bluetooth")
    $HEADLESS   && caps+=("headless")
    local caps_str
    caps_str=$(IFS=,; echo "${caps[*]:-none}")

    echo ""
    echo -e "${BOLD}${GREEN}SelenaCore is up.${NC}"
    echo ""
    echo "  Detected:  $OS_ID $OS_VERSION_ID ($OS_CODENAME), $ARCH"
    echo "             RAM ${RAM_GB} GB, $caps_str"
    echo "             Profile: $PROFILE"
    echo ""
    echo -e "  ${BOLD}Wizard:    http://${ip}/${NC}"
    echo ""
    echo "  The wizard will:"
    echo "    • let you pick STT / TTS / LLM models"
    echo "    • download them with progress"
    echo "    • create the admin user"
    echo "    • register the device with the platform"
    echo "    • install the native systemd services"
    echo ""
    echo "  Logs:"
    echo "    docker compose logs -f core"
    echo "    docker compose logs -f agent"
    echo ""
}

# ── Main ───────────────────────────────────────────────────────────

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-deps)        SKIP_DEPS=true; shift ;;
            --no-docker)        SKIP_DOCKER=true; shift ;;
            --build-frontend)   BUILD_FRONTEND=true; shift ;;
            --no-build)         BUILD_FRONTEND=false; shift ;;
            --dry-run)          DRY_RUN=true; shift ;;
            -h|--help)
                grep -E '^# ' "$0" | sed 's/^# //'
                exit 0 ;;
            *) err "Unknown option: $1"; exit 1 ;;
        esac
    done

    title "SelenaCore unified installer"
    require_root
    detect_environment

    if ! $SKIP_DEPS; then
        install_host_packages
        install_docker
        install_native_runtimes
    fi

    create_user_and_dirs
    install_repo
    bootstrap_config
    build_frontend

    if ! $SKIP_DOCKER; then
        start_docker_stack
    fi

    stage_systemd_units
    print_banner

    if $DRY_RUN; then
        echo ""
        log "Dry-run complete — no changes were made."
    fi
}

main "$@"
