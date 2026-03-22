# Deploying SelenaCore on Raspberry Pi

## Supported Platforms

| Device | Support | Recommended |
|--------|---------|-------------|
| Raspberry Pi 5 (8 GB) | ✅ Full | Yes — including LLM |
| Raspberry Pi 5 (4 GB) | ✅ Full | Yes |
| Raspberry Pi 4 (4/8 GB) | ✅ Full | Without LLM |
| Raspberry Pi 4 (2 GB) | ⚠️ Limited | LLM disabled |
| Debian x86-64 / ARM | ✅ | Yes |

---

## System Preparation

### 1. Operating System

Recommended: **Raspberry Pi OS 64-bit Lite** (no GUI) or **Debian 12 Bookworm**.

```bash
# Update the system
sudo apt update && sudo apt upgrade -y

# Required dependencies
sudo apt install -y \
    python3.11 python3.11-venv python3-pip \
    git curl wget \
    sqlite3 \
    ffmpeg \
    alsa-utils pulseaudio \
    iptables iptables-persistent \
    avahi-daemon avahi-utils \
    bluetooth bluez bluez-tools
```

### 2. Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Re-login or: newgrp docker

# Verify
docker run --rm hello-world
```

### 3. Clone the Repository

```bash
sudo mkdir -p /opt/selena-core
sudo chown $USER:$USER /opt/selena-core
cd /opt/selena-core

git clone https://github.com/dotradepro/SelenaCore.git .
```

---

## Configuration

### 4. Configure .env

```bash
cp .env.example .env
nano .env
```

Minimum settings:

```bash
CORE_PORT=7070
UI_PORT=80
CORE_DATA_DIR=/var/lib/selena
CORE_SECURE_DIR=/secure
CORE_LOG_LEVEL=INFO
DEBUG=false

# For platform connection (optional):
PLATFORM_API_URL=https://selenehome.tech/api/v1
PLATFORM_DEVICE_HASH=           # filled during platform registration
```

### 5. Create Directories

```bash
sudo mkdir -p /var/lib/selena
sudo mkdir -p /secure/tokens
sudo mkdir -p /secure/tls
sudo mkdir -p /secure/core_backup
sudo mkdir -p /var/log/selena

sudo chown -R $USER:$USER /var/lib/selena /secure /var/log/selena
sudo chmod 700 /secure
```

### 6. Initialize Storage

```bash
cd /opt/selena-core

# Create core.manifest (SHA256 of all core files)
python3 agent/manifest.py --init

# Generate HTTPS certificate
python3 scripts/generate_https_cert.py
```

---

## Launch

### Docker Compose (Recommended)

```bash
cd /opt/selena-core
docker compose up -d

# Check logs
docker compose logs -f core
docker compose logs -f agent

# Status
curl http://localhost:7070/api/v1/health
```

### Systemd (Without Docker)

```bash
# Install services
sudo cp smarthome-core.service /etc/systemd/system/
sudo cp smarthome-agent.service /etc/systemd/system/
sudo cp smarthome-modules.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable smarthome-core smarthome-agent smarthome-modules
sudo systemctl start smarthome-core

# Verify
sudo systemctl status smarthome-core
journalctl -u smarthome-core -f
```

---

## Onboarding Wizard

After first launch:

1. **With a monitor:** the browser opens automatically → `http://localhost:80`
2. **Without a monitor (headless):**
   - If Wi-Fi is available — the core creates an access point `SmartHome-Setup` / password `smarthome`
   - Connect from your phone → open `192.168.4.1`

### Wizard Steps

| Step | Description |
|------|-------------|
| `wifi` | Connect to Wi-Fi |
| `language` | Interface language (`uk`, `en`) |
| `device_name` | Device name |
| `timezone` | Time zone |
| `stt_model` | Speech recognition model |
| `tts_voice` | Text-to-speech voice |
| `admin_user` | Create administrator account |
| `platform` | Connect to SmartHome LK platform (optional) |
| `import` | Import devices (Home Assistant, Tuya, Philips Hue) |

---

## Audio

### Auto-detection

The core automatically detects available devices. To verify:

```bash
# List ALSA cards
arecord -l    # inputs
aplay -l      # outputs

# USB microphone should appear as card 1
```

### I2S Microphone (INMP441)

```bash
# Add to /boot/config.txt
echo "dtoverlay=googlevoicehat-soundcard" | sudo tee -a /boot/config.txt
sudo reboot

# Verify after reboot
arecord -l
```

### Bluetooth Speaker

```bash
# Via API (recommended)
curl -X POST http://localhost:7070/api/v1/system/bluetooth/pair \
  -H "Authorization: Bearer <token>" \
  -d '{"mac": "AA:BB:CC:DD:EE:FF"}'

# Or manually
bluetoothctl
  power on
  scan on
  pair AA:BB:CC:DD:EE:FF
  trust AA:BB:CC:DD:EE:FF
  connect AA:BB:CC:DD:EE:FF
  quit
```

### Force Audio Device Selection

```bash
# In .env
AUDIO_FORCE_INPUT=hw:2,0
AUDIO_FORCE_OUTPUT=bluez_sink.AA_BB_CC
```

---

## Updating

```bash
cd /opt/selena-core
git pull origin main

# Rebuild and restart
docker compose down
docker compose build
docker compose up -d

# Update core.manifest after core update
python3 agent/manifest.py --update
```

---

## Firewall (iptables)

```bash
# Apply rules from script
sudo bash scripts/setup_iptables.sh

# Manually — basic rules
sudo iptables -A INPUT -i lo -j ACCEPT
sudo iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT   # UI (LAN)
sudo iptables -A INPUT -p tcp --dport 7070 -j DROP     # Core API (localhost only)
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT     # SSH

# Save
sudo netfilter-persistent save
```

---

## Backup

### Local Backup to USB

```bash
curl -X POST http://localhost:7070/api/v1/backup/local \
  -H "Authorization: Bearer <token>" \
  -d '{"destination": "/media/usb0"}'
```

### Cloud Backup

Data is encrypted E2E (PBKDF2 + AES-256-GCM) before being sent to the platform:

```bash
curl -X POST http://localhost:7070/api/v1/backup/cloud \
  -H "Authorization: Bearer <token>"
```

---

## Monitoring

```bash
# System status
curl http://localhost:7070/api/v1/system/info | python3 -m json.tool

# Integrity Agent
curl http://localhost:7070/api/v1/integrity/status | python3 -m json.tool

# Hardware monitoring
curl http://localhost:7070/api/v1/system/hardware | python3 -m json.tool
```

---

## FAQ

**Q: Module won't start after installation**
A: Check status: `GET /api/v1/modules/{name}`. Status `ERROR` → check Docker logs: `docker logs selena-module-{name}`.

**Q: Голосовой ассистент не слышит wake-word**
A: Проверь аудио вход: `arecord -d 5 test.wav && aplay test.wav`. Если запись тихая — убедись, что усиление микрофона включено: `alsamixer`.

**Q: SAFE MODE — как выйти**
A: Причина — изменение файлов ядра. Автоматический откат из резервной копии должен сработать. Если нет:
```bash
sudo systemctl stop smarthome-core
python3 agent/manifest.py --restore
sudo systemctl start smarthome-core
```

**Q: Занят ли порт 7070**
A: `sudo lsof -i :7070` — найти и завершить конфликтующий процесс.
