# SelenaCore Deployment and Installation Guide

This guide covers hardware requirements, installation, configuration, and ongoing operations for the SelenaCore smart home hub.

---

## Supported Hardware

| Platform | Notes |
|----------|-------|
| Raspberry Pi 4/5 | 4GB+ RAM recommended |
| NVIDIA Jetson Orin Nano | GPU-accelerated TTS/STT support |
| Any Linux SBC (ARM64 or x86_64) | Tested on Ubuntu and Debian-based distros |

**Minimum requirements:**

- **2GB RAM** — sufficient for core functionality without a local LLM
- **4GB+ RAM** — required for full features including Ollama-based local LLM inference

---

## OS and Software Requirements

- Ubuntu 22.04+ or Raspberry Pi OS (Bookworm)
- Docker 24+ and Docker Compose v2
- Python 3.11+

---

## Installation

### Automatic Setup (Recommended)

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
sudo bash scripts/setup.sh
```

The setup script performs the following steps in order:

1. Install system packages (FFmpeg, PortAudio, VLC, ALSA utils)
2. Install Docker and Docker Compose
3. Install Python 3.11 and pip
4. Create data directories (`/var/lib/selena`, `/secure`)
5. Generate module authentication tokens
6. Build Docker images
7. Start all services
8. Display the access URL

### Manual Setup

```bash
# Clone the repository
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore

# Copy and edit the environment file
cp .env.example .env
# Edit .env with your settings (see Environment Variables below)

# Build and start
docker compose build
docker compose up -d
```

---

## Docker Architecture

The `docker-compose.yml` file defines two services.

### selena-core (main service)

The primary container running the SelenaCore application.

- **Image:** Built from `Dockerfile.core` (base: `python:3.11-slim`)
- **Network mode:** `host` (required for audio and device access)
- **Privileged:** `true` (required for hardware access)
- **Exposed ports:**
  - `80` — Unified API + Web UI (single process)
  - `443` — HTTPS (TLS proxy to :80)
- **Volumes:**
  - `/var/run/docker.sock` — Docker socket for managing module containers
  - `selena_data:/var/lib/selena` — Database, voice models, backups
  - `selena_secure:/secure` — Encrypted tokens and keys
  - `/dev/snd` — ALSA sound devices for audio input/output
  - Ollama models directory (if configured)
- **Health check:** `GET /api/v1/health` every 30 seconds
- **Bundled software:** FFmpeg, PortAudio, VLC, ALSA utils (aplay, arecord, amixer)
- **External services (native on host):** Piper TTS (`piper-tts.service`), llama.cpp / Ollama

### selena-agent (integrity agent)

A separate container that continuously monitors core integrity.

- Performs SHA256 hash verification of core files every 30 seconds
- On integrity violation: stops modules, sends notification, initiates rollback, enters **SAFE MODE**

### GPU Support (NVIDIA Jetson)

For GPU-accelerated container features, start with the GPU override file:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

### Piper TTS Native Service

Piper TTS runs natively on the host (not in Docker) for direct GPU access and lower memory usage.

```bash
# Install Piper TTS
pip3 install --user piper-tts aiohttp

# For GPU on Jetson (JetPack 6, CUDA 12.x):
pip3 install --user onnxruntime-gpu --extra-index-url https://pypi.jetson-ai-lab.io/jp6/cu126
pip3 install --user "numpy<2"
sudo ln -sf /usr/lib/aarch64-linux-gnu/libcudnn.so.9 /usr/lib/aarch64-linux-gnu/libcudnn.so
# Or use the automated script: bash scripts/build-onnxruntime-gpu.sh

# Deploy systemd service
sudo cp scripts/piper-tts.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now piper-tts

# Verify
curl http://localhost:5100/health
# → "device": "gpu", "cuda_available": true
```

> **Note:** PyPI `onnxruntime-gpu` does NOT have aarch64 wheels. Must use NVIDIA Jetson AI Lab index.

**Device modes:** `--device auto` (default, detect GPU), `--device cpu`, `--device gpu`

### llama.cpp / Ollama

LLM inference runs natively on host for GPU layer offloading.

```bash
# Start with GPU (default)
bash scripts/llamacpp-start.sh /path/to/model.gguf 8081 999

# CPU only
LLAMACPP_GPU_LAYERS=0 bash scripts/llamacpp-start.sh /path/to/model.gguf
```

---

## Environment Variables

All configuration is managed through the `.env` file in the project root. Copy `.env.example` to `.env` and adjust as needed.

| Variable | Default | Description |
|----------|---------|-------------|
| `CORE_PORT` | `80` | API server port |
| `CORE_DATA_DIR` | `/var/lib/selena` | Data directory (DB, models) |
| `CORE_SECURE_DIR` | `/secure` | Encrypted secrets directory |
| `CORE_LOG_LEVEL` | `INFO` | Log level |
| `DEBUG` | `false` | Enable debug mode and Swagger UI |
| `PLATFORM_API_URL` | `https://smarthome-lk.com/api/v1` | Cloud platform URL |
| `PLATFORM_DEVICE_HASH` | *(empty)* | Device identification hash |
| `UI_PORT` | `80` | Web UI port |
| `UI_HTTPS` | `true` | Enable HTTPS for UI |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Docker socket path |
| `MODULE_CONTAINER_IMAGE` | `smarthome-modules:latest` | User module container image |
| `GOOGLE_CLIENT_ID` | *(empty)* | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | *(empty)* | Google OAuth secret |
| `TUYA_CLIENT_ID` | *(empty)* | Tuya integration client ID |
| `TUYA_CLIENT_SECRET` | *(empty)* | Tuya integration secret |
| `TAILSCALE_AUTH_KEY` | *(empty)* | Tailscale VPN auth key |
| `GEMINI_API_KEY` | *(empty)* | Cloud LLM fallback key |
| `DEV_MODULE_TOKEN` | *(empty)* | Development token for testing |
| `OLLAMA_MODELS_DIR` | *(empty)* | Ollama model storage directory |

---

## core.yaml Configuration

The main configuration file is located at `/opt/selena-core/config/core.yaml`. See [Configuration Reference](configuration.md) for all available options.

---

## Systemd Services

To run SelenaCore as a system service that starts on boot, install the following unit file.

### smarthome-core.service

```ini
# /etc/systemd/system/smarthome-core.service
[Unit]
Description=SelenaCore Smart Home Hub
After=docker.service
Requires=docker.service

[Service]
WorkingDirectory=/opt/selena-core
ExecStart=/usr/bin/docker compose up
ExecStop=/usr/bin/docker compose down
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable smarthome-core.service
sudo systemctl start smarthome-core.service
```

### Additional Services

| Service | Purpose |
|---------|---------|
| `smarthome-agent.service` | Integrity monitoring agent |
| `smarthome-modules.service` | Module bus gateway |
| `getty@tty1` (with override) | Headless kiosk mode (see [Kiosk Setup](kiosk-setup.md)) |
| `vosk-server.service` | Vosk STT server |
| `piper-tts.service` | Piper TTS server (native, GPU) |

### Headless Kiosk (Recommended for Production)

For Jetson and Raspberry Pi deployments, disable the desktop environment and run Chromium via Xorg kiosk. This saves ~1 GB RAM.

```bash
# Disable desktop, enable kiosk
sudo systemctl set-default multi-user.target
sudo systemctl disable gdm3
```

See [Kiosk Setup](kiosk-setup.md) for full instructions including getty autologin, ALSA audio, and Xorg configuration.

---

## Onboarding Wizard

On first start, SelenaCore enters setup mode and walks the user through initial configuration.

1. Creates a WiFi access point: `SmartHome-Setup`
2. Opens a web wizard at `http://192.168.4.1`
3. Wizard steps:
   - Language selection
   - WiFi network configuration
   - Device name
   - Voice engine selection
   - User profile creation
   - Display settings
   - Platform link
4. After completion, the system restarts in normal mode

---

## Operations

### Health Check

Verify the system is running correctly:

```bash
curl http://localhost/api/v1/health
```

Expected response:

```json
{
  "status": "ok",
  "version": "...",
  "mode": "normal",
  "uptime": 12345,
  "integrity": "ok"
}
```

### Viewing Logs

```bash
# Follow core logs in real time
docker compose logs -f selena-core

# Filter logs for a specific module
docker compose logs -f selena-core | grep "module-name"

# View log files on disk
ls /var/log/selena/
```

### Data Directories

| Path | Contents |
|------|----------|
| `/var/lib/selena/` | SQLite database, voice models, backups |
| `/var/lib/selena/models/vosk/` | Vosk STT models |
| `/var/lib/selena/models/piper/` | Piper TTS models |
| `/secure/` | Encrypted tokens, AES keys |
| `/secure/module_tokens/` | Module authentication tokens |

---

## Updating

Pull the latest changes and rebuild:

```bash
cd /opt/selena-core
git pull
docker compose build
docker compose up -d
```

Alternatively, use the `update_manager` system module for automatic over-the-air updates.

---

## Backup

The `backup_manager` system module handles automated backups:

- **Local backups:** SQLite database and configuration files
- **Cloud backups:** To configured remote storage

For manual backup, copy the data and secrets directories:

```bash
sudo cp -r /var/lib/selena/ /path/to/backup/selena_data/
sudo cp -r /secure/ /path/to/backup/selena_secure/
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **Port 80 in use** | Change `CORE_PORT` in `.env` and restart |
| **No audio output or input** | Check `/dev/snd` is mounted in `docker-compose.yml`; verify devices with `aplay -l` and `arecord -l` inside the container; use `plughw:X,Y` device IDs for ALSA |
| **Module will not connect** | Verify `MODULE_TOKEN` and `SELENA_BUS_URL` are set correctly in the module environment |
| **System entered Safe Mode** | Check integrity agent logs (`docker compose logs selena-agent`); verify core file hashes match expected values |
| **Docker permission denied** | Ensure the current user is in the `docker` group, or run with `sudo` |
| **Ollama models not loading** | Verify `OLLAMA_MODELS_DIR` points to an existing directory with sufficient disk space |
