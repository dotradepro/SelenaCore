#!/usr/bin/env bash
# SelenaCore Installer — auto-detect hardware, install components
# Usage:
#   bash install.sh              # auto-detect
#   bash install.sh --profile jetson
#   bash install.sh --profile raspberry
#   bash install.sh --profile linux_cuda
#   bash install.sh --profile linux_cpu
#   bash install.sh --update
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="/opt/selena-core"
DATA_DIR="/var/lib/selena"
LOG_DIR="/var/log/selena"
WHISPER_DIR="/opt/whisper.cpp"
WHISPER_PORT=9000
PIPER_PORT=5100

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[x]${NC} $*" >&2; }

# ── Hardware Detection ──────────────────────────────────────────────

detect_hardware() {
    HW_ARCH=$(uname -m)
    HW_RAM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
    HW_RAM_GB=$(( HW_RAM_MB / 1024 ))
    HW_JETSON=false
    HW_RASPBERRY=false
    HW_CUDA=false
    HW_MODEL=""

    # Check device tree
    if [ -f /proc/device-tree/model ]; then
        HW_MODEL=$(tr -d '\0' < /proc/device-tree/model)
        if echo "$HW_MODEL" | grep -qi "jetson"; then
            HW_JETSON=true
        elif echo "$HW_MODEL" | grep -qi "raspberry"; then
            HW_RASPBERRY=true
        fi
    fi

    # Check CUDA
    if command -v nvidia-smi &>/dev/null || [ -d /usr/local/cuda ]; then
        HW_CUDA=true
    fi

    echo ""
    log "Hardware detected:"
    echo "  Architecture: $HW_ARCH"
    echo "  RAM:          ${HW_RAM_GB} GB"
    echo "  Device:       ${HW_MODEL:-Unknown}"
    echo "  CUDA:         $HW_CUDA"
    echo "  Jetson:       $HW_JETSON"
    echo "  Raspberry Pi: $HW_RASPBERRY"
}

# ── Profile Selection ───────────────────────────────────────────────

select_profile() {
    if $HW_JETSON; then
        PROFILE="jetson"
    elif $HW_RASPBERRY; then
        PROFILE="raspberry"
    elif $HW_CUDA; then
        PROFILE="linux_cuda"
    else
        PROFILE="linux_cpu"
    fi

    echo ""
    log "Recommended profile: ${BLUE}${PROFILE}${NC}"
    echo ""
    echo "  1) ${PROFILE} (recommended)"
    echo "  2) minimal (core only, no STT/TTS/LLM)"
    echo "  3) Choose manually"
    echo ""
    read -rp "Select [1]: " choice
    case "${choice:-1}" in
        1) ;; # keep recommended
        2) PROFILE="minimal" ;;
        3)
            echo ""
            echo "  Available profiles: jetson, raspberry, linux_cuda, linux_cpu, minimal"
            read -rp "  Profile: " PROFILE
            ;;
    esac
    log "Using profile: ${BLUE}${PROFILE}${NC}"
}

# ── Component Installers ────────────────────────────────────────────

install_system_deps() {
    log "Installing system dependencies..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        python3 python3-pip python3-venv \
        git curl wget cmake build-essential \
        ffmpeg libsndfile1 \
        arp-scan arping \
        pulseaudio-utils \
        sqlite3 \
        2>/dev/null || true
}

install_whisper_cpp() {
    local build_cuda="$1"  # "ON" or "OFF"
    if [ -f "$WHISPER_DIR/build/bin/whisper-server" ]; then
        log "whisper.cpp already built, skipping"
        return
    fi

    log "Building whisper.cpp (CUDA=$build_cuda)..."
    sudo mkdir -p "$WHISPER_DIR"
    sudo chown "$(whoami)" "$WHISPER_DIR"
    git clone --depth 1 https://github.com/ggerganov/whisper.cpp.git "$WHISPER_DIR" 2>/dev/null || true
    cd "$WHISPER_DIR"
    cmake -B build -DWHISPER_CUDA="$build_cuda" -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j"$(nproc)"

    # Download model
    if [ ! -f "$WHISPER_DIR/models/ggml-small.bin" ]; then
        log "Downloading Whisper small model (~460 MB)..."
        bash ./models/download-ggml-model.sh small
    fi
    cd "$SCRIPT_DIR"
}

install_faster_whisper() {
    log "Installing faster-whisper..."
    pip3 install --user faster-whisper 2>/dev/null || pip3 install faster-whisper
}

install_ollama() {
    if command -v ollama &>/dev/null; then
        log "Ollama already installed"
    else
        log "Installing Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh
    fi

    local model="${1:-qwen2.5:3b}"
    log "Pulling model: $model..."
    ollama pull "$model" 2>/dev/null || warn "Failed to pull $model (try manually: ollama pull $model)"
}

install_piper() {
    if command -v piper &>/dev/null || [ -f /usr/local/bin/piper ]; then
        log "Piper TTS already installed"
    else
        log "Installing Piper TTS..."
        pip3 install --user piper-tts 2>/dev/null || pip3 install piper-tts || warn "Piper install failed"
    fi

    # Download EN fallback voice (~5MB) for multilingual TTS support
    local piper_models="${PIPER_MODELS_DIR:-$DATA_DIR/models/piper}"
    mkdir -p "$piper_models"
    local fallback_voice="en_US-amy-low"
    if [ ! -f "$piper_models/${fallback_voice}.onnx" ]; then
        log "Downloading Piper fallback voice: $fallback_voice (~5MB)..."
        local base_url="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/low"
        curl -sL "$base_url/en_US-amy-low.onnx" -o "$piper_models/${fallback_voice}.onnx" || warn "Fallback voice download failed"
        curl -sL "$base_url/en_US-amy-low.onnx.json" -o "$piper_models/${fallback_voice}.onnx.json" 2>/dev/null
        log "Fallback voice downloaded: $fallback_voice"
    fi
}

install_selenacore() {
    log "Installing SelenaCore..."
    sudo mkdir -p "$INSTALL_DIR" "$DATA_DIR" "$LOG_DIR"

    # Copy files
    if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
        sudo cp -r "$SCRIPT_DIR"/{core,system_modules,config,agent,sdk} "$INSTALL_DIR/" 2>/dev/null || true
        sudo cp "$SCRIPT_DIR"/requirements.txt "$INSTALL_DIR/" 2>/dev/null || true
    fi

    # Install Python dependencies
    cd "$INSTALL_DIR"
    pip3 install -r requirements.txt 2>/dev/null || warn "Some Python deps failed"
    cd "$SCRIPT_DIR"
}

# ── Systemd Services ───────────────────────────────────────────────

install_systemd_services() {
    log "Installing systemd services..."

    # whisper-server service
    if [ -f "$WHISPER_DIR/build/bin/whisper-server" ]; then
        # Write default env file for whisper-server
        sudo mkdir -p "$DATA_DIR"
        echo "WHISPER_MODEL=ggml-small" | sudo tee "$DATA_DIR/whisper-server.env" > /dev/null

        sudo tee /etc/systemd/system/whisper-server.service > /dev/null <<EOF
[Unit]
Description=Whisper.cpp STT Server
After=network.target

[Service]
Type=simple
EnvironmentFile=-$DATA_DIR/whisper-server.env
Environment=WHISPER_MODEL=ggml-small
ExecStart=$WHISPER_DIR/build/bin/whisper-server \\
    --model $WHISPER_DIR/models/\${WHISPER_MODEL}.bin \\
    --host 0.0.0.0 --port $WHISPER_PORT --language auto
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable whisper-server
        log "whisper-server.service installed"
    fi
}

# ── Config Generation ───────────────────────────────────────────────

generate_config() {
    local profile="$1"
    local config_file="$DATA_DIR/core.yaml"

    if [ -f "$config_file" ]; then
        warn "Config already exists: $config_file (skipping)"
        return
    fi

    log "Generating core.yaml for profile: $profile"
    local stt_provider="auto"
    local llm_model="qwen2.5:3b"

    case "$profile" in
        jetson)
            stt_provider="auto"  # will find whisper.cpp
            llm_model="qwen2.5:3b"
            ;;
        raspberry)
            stt_provider="faster_whisper"
            if [ "$HW_RAM_GB" -lt 4 ]; then
                llm_model="qwen2.5:1.5b"
            else
                llm_model="qwen2.5:3b"
            fi
            ;;
        linux_cuda)
            stt_provider="auto"
            llm_model="qwen2.5:3b"
            ;;
        linux_cpu)
            stt_provider="faster_whisper"
            llm_model="qwen2.5:3b"
            ;;
    esac

    sudo mkdir -p "$(dirname "$config_file")"
    sudo tee "$config_file" > /dev/null <<EOF
core:
  host: "0.0.0.0"
  port: 7070
  data_dir: "$DATA_DIR"
  log_level: "INFO"

stt:
  provider: "$stt_provider"
  whisper_cpp:
    host: "http://localhost:$WHISPER_PORT"
  faster_whisper:
    model: "small"
    device: "auto"
    compute_type: "auto"

ai:
  conversation:
    provider: "local"
    local:
      host: "http://localhost:11434"
      model: "$llm_model"
      options:
        temperature: 0.1
        num_predict: 80

voice:
  tts_voice: "uk_UA-ukrainian_tts-medium"
  tts_fallback_voice: "en_US-amy-low"
  wake_word_model: "привіт селена"
  stt_silence_timeout: 1.0

system:
  device_name: "SelenaCore"
  language: "uk"
  timezone: "Europe/Kyiv"
EOF
    log "Config written to $config_file"
}

# ── Profile Runners ─────────────────────────────────────────────────

run_profile_jetson() {
    install_system_deps
    install_whisper_cpp "ON"
    install_ollama "qwen2.5:3b"
    install_piper
    install_selenacore
    install_systemd_services
    generate_config "jetson"
}

run_profile_raspberry() {
    install_system_deps
    install_faster_whisper
    local model="qwen2.5:3b"
    if [ "$HW_RAM_GB" -lt 4 ]; then
        model="qwen2.5:1.5b"
    fi
    install_ollama "$model"
    install_piper
    install_selenacore
    generate_config "raspberry"
}

run_profile_linux_cuda() {
    install_system_deps
    install_whisper_cpp "ON"
    install_ollama "qwen2.5:3b"
    install_piper
    install_selenacore
    install_systemd_services
    generate_config "linux_cuda"
}

run_profile_linux_cpu() {
    install_system_deps
    install_faster_whisper
    install_ollama "qwen2.5:3b"
    install_piper
    install_selenacore
    generate_config "linux_cpu"
}

run_profile_minimal() {
    install_system_deps
    install_selenacore
    generate_config "minimal"
}

# ── Verification ────────────────────────────────────────────────────

verify_installation() {
    echo ""
    log "Verifying installation..."
    local ok=true

    # Check whisper-server
    if [ -f "$WHISPER_DIR/build/bin/whisper-server" ]; then
        echo -e "  whisper.cpp binary:  ${GREEN}OK${NC}"
    else
        echo -e "  whisper.cpp binary:  ${YELLOW}not installed${NC}"
    fi

    if [ -f "$WHISPER_DIR/models/ggml-small.bin" ]; then
        echo -e "  Whisper model:       ${GREEN}OK${NC}"
    else
        echo -e "  Whisper model:       ${YELLOW}not found${NC}"
    fi

    # Check Ollama
    if command -v ollama &>/dev/null; then
        echo -e "  Ollama:              ${GREEN}OK${NC}"
    else
        echo -e "  Ollama:              ${YELLOW}not installed${NC}"
    fi

    # Check Python deps
    if python3 -c "import fastapi" &>/dev/null; then
        echo -e "  FastAPI:             ${GREEN}OK${NC}"
    else
        echo -e "  FastAPI:             ${RED}MISSING${NC}"
        ok=false
    fi

    echo ""
    if $ok; then
        log "Installation complete!"
        echo ""
        echo "  Next steps:"
        echo "    1. Start whisper-server:  sudo systemctl start whisper-server"
        echo "    2. Start SelenaCore:      docker compose up -d"
        echo "    3. Open UI:              http://localhost:80"
        echo ""
    else
        err "Some components are missing. Check the output above."
    fi
}

# ── Main ────────────────────────────────────────────────────────────

main() {
    echo ""
    echo "  SelenaCore Installer v2.0"
    echo "  ========================"
    echo ""

    local profile=""
    local update_only=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --profile) profile="$2"; shift 2 ;;
            --update)  update_only=true; shift ;;
            --help|-h)
                echo "Usage: bash install.sh [--profile NAME] [--update]"
                echo "Profiles: jetson, raspberry, linux_cuda, linux_cpu, minimal"
                exit 0 ;;
            *) err "Unknown option: $1"; exit 1 ;;
        esac
    done

    detect_hardware

    if $update_only; then
        log "Updating SelenaCore..."
        install_selenacore
        verify_installation
        exit 0
    fi

    if [ -z "$profile" ]; then
        select_profile
    else
        PROFILE="$profile"
        log "Using profile: $PROFILE"
    fi

    case "$PROFILE" in
        jetson)     run_profile_jetson ;;
        raspberry)  run_profile_raspberry ;;
        linux_cuda) run_profile_linux_cuda ;;
        linux_cpu)  run_profile_linux_cpu ;;
        minimal)    run_profile_minimal ;;
        *) err "Unknown profile: $PROFILE"; exit 1 ;;
    esac

    verify_installation
}

main "$@"
