# Display and Kiosk Mode Configuration

## Overview

SelenaCore supports three display modes depending on the hardware:

| Mode | When | How |
|------|------|-----|
| **Kiosk (Xorg)** | Headless + HDMI screen (Jetson/RPi) | getty autologin → xinit → Chromium |
| **Desktop window** | GNOME/KDE running | Chromium kiosk window |
| **TUI** | No display at all | Python terminal UI with QR code |

**Recommended for production:** Headless kiosk (no desktop environment). Saves ~1 GB RAM.

---

## Headless Kiosk Setup (Recommended)

This is the production setup for Jetson and Raspberry Pi devices. GNOME/GDM3 are disabled, Chromium runs directly on Xorg via `xinit`.

### 1. Disable Desktop Environment

```bash
# Switch to headless boot
sudo systemctl disable gdm3
sudo systemctl set-default multi-user.target

# Disable unnecessary services
sudo systemctl mask update-manager.service
# Optional: sudo systemctl disable cups cups-browsed ModemManager

# Reboot
sudo reboot

# Verify
systemctl get-default   # → multi-user.target
```

### 2. Fix Runtime Directory (Permanent)

Create `/etc/tmpfiles.d/fix-runtime-dir.conf`:

```ini
d /run/user/1000 0700 <your-user> <your-user> -
```

Apply:

```bash
sudo cp setup/fix-runtime-dir.conf /etc/tmpfiles.d/
sudo systemd-tmpfiles --create /etc/tmpfiles.d/fix-runtime-dir.conf
```

### 3. Configure Getty Autologin on TTY1

Create override at `/etc/systemd/system/getty@tty1.service.d/override.conf`:

```ini
[Service]
ExecStartPre=-/bin/bash -c 'mkdir -p /run/user/1000 && chown <user>:<user> /run/user/1000 && chmod 700 /run/user/1000'
ExecStart=
ExecStart=-/sbin/agetty --autologin <your-user> --noclear %I $TERM
```

Install:

```bash
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d/
sudo cp setup/getty-autologin-override.conf /etc/systemd/system/getty@tty1.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart getty@tty1.service
```

### 4. Kiosk Startup Script

The file `scripts/kiosk-start.sh` is launched automatically from `~/.bash_profile` on tty1:

```bash
# ~/.bash_profile
if [ -f "$HOME/.profile" ]; then
    . "$HOME/.profile"
fi

# Auto-start kiosk on tty1 only
if [ "$(tty)" = "/dev/tty1" ]; then
    exec /path/to/SelenaCore/scripts/kiosk-start.sh
fi
```

The script:
1. Waits for SelenaCore API to be ready (up to 60 seconds)
2. Writes a temporary `.xinitrc` (disables screen blanking, hides cursor)
3. Launches `xinit` with Chromium in kiosk mode on `vt1`

### 5. PulseAudio for Voice

In headless mode, PulseAudio starts automatically via the user session (`systemd --user`). The Docker container accesses the host PulseAudio socket via volume mount:

```yaml
# docker-compose.yml
volumes:
  - /run/user/1000/pulse:/run/user/1000/pulse:rw
  - ~/.config/pulse/cookie:/root/.config/pulse/cookie:ro
environment:
  - PULSE_SERVER=unix:/run/user/1000/pulse/native
```

**Important:** If the container starts before PulseAudio, restart it after kiosk boots:

```bash
docker compose restart core
```

---

## Boot Sequence

```
systemd (multi-user.target)
  ├── getty@tty1 (autologin)
  │     └── .bash_profile
  │           └── kiosk-start.sh
  │                 ├── wait for API health
  │                 └── xinit → Xorg + Chromium kiosk
  ├── docker (selena-core container)
  │     └── FastAPI :80 (unified API + SPA) + TLS proxy :443
  ├── vosk-server.service
  │     └── Vosk STT (native, no container)
  └── pulseaudio (user session)
        └── audio I/O for voice
```

---

## NVIDIA Jetson Notes

- **Wayland (cage) does not work** on Jetson Tegra DRM — use Xorg instead
- NVIDIA Tegra GPU driver requires Xorg; `wlroots` cannot open `/dev/dri/card0`
- Chromium uses GPU rasterization via `--enable-gpu-rasterization`

---

## RAM Comparison

| Mode | OS RAM | Available for AI |
|------|--------|-----------------|
| Full GNOME desktop | ~1.7 GB | ~5.7 GB |
| **Headless kiosk (Xorg)** | **~0.7 GB** | **~6.7 GB** |
| No display (SSH only) | ~0.65 GB | ~6.75 GB |

On 8 GB Jetson, headless saves ~1 GB for Ollama LLM models.

---

## Refreshing the Kiosk Display

After deploying frontend changes:

```bash
# With wtype (Wayland) — NOT available on Xorg kiosk
# wtype -k F5

# With xdotool (Xorg kiosk)
DISPLAY=:0 xdotool key F5

# Nuclear option — restart the entire kiosk
sudo systemctl restart getty@tty1.service
```

---

## Reverting to Desktop Mode

```bash
sudo systemctl set-default graphical.target
sudo systemctl enable gdm3
sudo reboot
```

---

## TUI Mode (No Display)

When no HDMI is connected and kiosk is not configured:

- System boots to headless TTY
- Managed entirely via SSH
- QR code displayed via `tty_status.py` for mobile setup
- `smarthome-display.service` runs TUI status on tty1

---

## Display Configuration in core.yaml

```yaml
ui:
  host: "0.0.0.0"
  port: 80
  https: true
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **Screen blank after boot** | Check `systemctl status getty@tty1` and `journalctl -u getty@tty1` |
| **Chromium not starting** | Verify Xorg is installed: `which Xorg` and check `/tmp/.xinitrc-kiosk` |
| **No audio in container** | PulseAudio may not be running yet — restart container: `docker compose restart core` |
| **cage: "Found 0 GPUs"** | Jetson Tegra DRM is incompatible with cage/wlroots — use Xorg kiosk instead |
| **getty restart loop** | Check `/run/user/1000` ownership: `stat -c '%U' /run/user/1000` — must match your user |
| **Touch not working** | Add user to `input` group: `sudo usermod -aG input <user>` |
| **Cursor visible** | Install `unclutter`: `sudo apt install unclutter` |
