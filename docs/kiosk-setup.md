# SelenaCore — Kiosk & Display Setup

Guide for setting up auto-launch of the SelenaCore interface on a physical display.
Supported platforms: **Jetson Orin**, Raspberry Pi 4/5, any Linux SBC with Ubuntu 22.04+.

---

## Quick Start (automatic)

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
sudo bash scripts/setup.sh
```

The script will do everything automatically: install dependencies, build images, configure the service.
After completion — log out and log back in (to update groups), the kiosk will start automatically.

---

## What happens on startup

```
systemd
  └─ selena-display.service
       └─ scripts/start-display.sh
            ├─ Waits for core container (healthy)
            ├─ Waits for UI (http://localhost)
            ├─ Determines display mode:
            │     desktop  → Chromium --kiosk via DE (GNOME/KDE)
            │     kiosk    → cage + Chromium (Wayland, no DE)
            │     tty      → Python TUI with QR code
            └─ Launches the selected mode
```

### Display modes

| Mode | Condition | Description |
|------|-----------|-------------|
| `desktop` | `$DISPLAY` or `$WAYLAND_DISPLAY` is set | DE is running (GNOME, KDE, etc.) — Chromium opens within it |
| `kiosk` | cage + chromium + `/dev/dri/card*` | Wayland compositor without DE, direct output to screen |
| `tty` | fallback | Text interface with QR code for connecting |

---

## Manual installation (step by step)

### 1. System dependencies

```bash
# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker

# Kiosk (Wayland without DE)
sudo apt install -y cage chromium-browser seatd

# Seat manager
sudo systemctl enable --now seatd
```

### 2. User groups

```bash
sudo usermod -aG docker,_seatd,video,input,render $USER
```

> Changes take effect after logging out and back in.

### 3. Log directory

```bash
sudo mkdir -p /var/log/selena
sudo chown $USER:$USER /var/log/selena
```

### 4. Configuration

```bash
cp .env.example .env
# Edit .env if needed
nano .env
```

### 5. Build and start containers

```bash
docker compose build
docker compose up -d
```

### 6. Install display service

```bash
sudo bash scripts/setup.sh --no-docker
# or manually:
sudo bash scripts/install-display.sh
```

---

## Service structure

`/etc/systemd/system/selena-display.service`:

```ini
[Unit]
Description=SelenaCore Kiosk Display
After=network.target docker.service seatd.service
Requires=docker.service
Wants=seatd.service

[Service]
Type=simple
User=YOUR_USER
ExecStart=/path/to/SelenaCore/scripts/start-display.sh
Restart=on-failure
RestartSec=5
Environment=LIBSEAT_BACKEND=seatd
Environment=WLR_BACKENDS=drm,libinput
Environment=WLR_NO_HARDWARE_CURSORS=1

[Install]
WantedBy=multi-user.target
```

---

## Service management

```bash
# Status
sudo systemctl status selena-display.service

# Live logs
journalctl -u selena-display.service -f

# Restart
sudo systemctl restart selena-display.service

# Stop
sudo systemctl stop selena-display.service

# Log file
tail -f /var/log/selena/display.log
```

---

## Troubleshooting

### Kiosk won't start: Permission denied (tty / seat)

```bash
# Check groups
id $USER
# Should include: _seatd, video, input, render

# Check seatd
sudo systemctl status seatd

# Check DRI availability
ls /dev/dri/
```

**Solution**: make sure the user is in the required groups and you have logged out/in.

### Chromium not found

```bash
# Check
which chromium || which chromium-browser

# Install
sudo apt install -y chromium-browser
```

On Ubuntu 22.04 `chromium-browser` is an apt wrapper that installs the **snap** version of Chromium.
Actual binary: `/snap/bin/chromium`.

### cage: failed to start a session

```bash
# Check backend
LIBSEAT_BACKEND=seatd cage -- echo ok

# If seatd doesn't work — try logind
LIBSEAT_BACKEND=logind cage -- echo ok
```

### Logs on startup failure

```bash
journalctl -u selena-display.service --no-pager -n 50
journalctl -u seatd --no-pager -n 20
```

---

## Jetson Orin — specifics

| Feature | Solution |
|---------|----------|
| ARM64 (aarch64) | All packages are installed from `ports.ubuntu.com` |
| NVIDIA Tegra DRM | `/dev/dri/card0` (Tegra), `/dev/dri/card1` (GPU) — cage uses card0 |
| `WLR_NO_HARDWARE_CURSORS=1` | Required — software cursor for compatibility |
| snap-Chromium | `/snap/bin/chromium` — `find_chromium()` detects automatically |
| seatd vs logind | seatd is preferred for system services without a logind session |

---

## Raspberry Pi — specifics

| Feature | Solution |
|---------|----------|
| No DE (lite image) | Kiosk mode works via cage |
| With DE (Raspberry Pi OS Desktop) | Desktop mode works, `$DISPLAY=:0` |
| Framebuffer `/dev/fb0` | cage detects it as fallback DRI |

---

## FAQ

**Q: Can I use a different browser?**
A: In theory — any Wayland browser. Replace `chromium` in `find_chromium()`.

**Q: How to change the UI URL?**
A: In the service: `Environment=SELENA_UI_URL=http://your-ip:port`

**Q: How to disable kiosk and return to normal desktop?**
```bash
sudo systemctl disable selena-display.service
sudo systemctl stop selena-display.service
```

**Q: How to update the project?**
```bash
git pull
docker compose build
docker compose up -d
```
