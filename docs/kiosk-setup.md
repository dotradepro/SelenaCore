# Display and Kiosk Mode Configuration

## Overview

SelenaCore supports four display modes depending on the hardware:

| Mode | When | How |
|------|------|-----|
| **Kiosk (Wayland/cog)** | Headless + HDMI screen (RPi/generic) | cage + cog (WPE WebKit), ~50 MB |
| **Kiosk (Xorg)** | Headless + HDMI screen (Jetson) | getty autologin → xinit → Chromium |
| **Desktop window** | GNOME/KDE running | Chromium kiosk window |
| **TUI** | No display at all | Python terminal UI with QR code |

**Recommended for production:** Headless kiosk with cog (WPE WebKit). Saves ~1 GB RAM vs desktop and ~250 MB vs Chromium kiosk.

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

### 4. Kiosk Startup

Kiosk display is now installed automatically by the wizard's
`install_native_services` provisioning step (via [scripts/install-systemd.sh](../scripts/install-systemd.sh)).
The script detects whether `cage` and a connected DRM output are present and
generates a `selena-display.service` unit pointing at
[scripts/start-display.sh](../scripts/start-display.sh), which then launches
`cage + cog` (WPE WebKit) in kiosk mode on the active VT. If `cog` is not
installed, it falls back to `cage + chromium`. You can force a specific browser
via the `SELENA_KIOSK_BROWSER` environment variable.

No manual `~/.bash_profile` or autologin agetty hack is needed anymore.

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
  ├── selena-display.service
  │     └── start-display.sh
  │           ├── kiosk: cage → cog (WPE WebKit, preferred) or Chromium
  │           ├── desktop: Chromium kiosk window in existing DE
  │           └── tty: Python TUI with QR code
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
- cog (WPE WebKit) shares the same Tegra DRM limitation — it runs inside cage
- Chromium uses GPU rasterization via `--enable-gpu-rasterization`
- On Jetson, use `kiosk-start.sh` (Xorg path) or desktop mode

---

## RAM Comparison

| Mode | OS RAM | Available for AI |
|------|--------|-----------------|
| Full GNOME desktop | ~1.7 GB | ~5.7 GB |
| Headless kiosk (Chromium/Xorg) | ~0.7 GB | ~6.7 GB |
| **Headless kiosk (cog/Wayland)** | **~0.5 GB** | **~7.0 GB** |
| No display (SSH only) | ~0.65 GB | ~6.75 GB |

On 8 GB Jetson, headless saves ~1 GB for Ollama LLM models.

---

## Refreshing the Kiosk Display

After deploying frontend changes:

```bash
# With wtype (Wayland kiosk — works with both cog and Chromium)
sudo XDG_RUNTIME_DIR=/run/user/0 WAYLAND_DISPLAY=wayland-0 wtype -k F5

# With xdotool (Xorg kiosk — Jetson)
DISPLAY=:0 xdotool key F5

# Nuclear option — restart the entire kiosk
sudo systemctl restart selena-display.service
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
- Status TUI can be run manually:
  `docker compose exec core python -m system_modules.ui_core.tty_status`

---

## Display Configuration in core.yaml

```yaml
ui:
  host: "0.0.0.0"
  port: 80
  https: true
```

---

## DietPi / Minimal Distros — KMS Not Enabled

Some minimal distributions (DietPi, Armbian minimal, custom images) ship
with the KMS video driver **disabled** by default.  Without KMS the kernel
does not create `/sys/class/drm/`, so `install.sh` sees no display and
falls back to **headless** mode — even when an HDMI screen is physically
connected to the Raspberry Pi.

**Symptoms:**
- `install.sh` reports `Display=false Headless=true` on a Pi with a screen
- `cage`, `cog`, `wtype` are not installed
- `selena-display.service` is not created
- `/sys/class/drm/` does not exist

**Automatic fix (install.sh ≥ 0.3):**

Starting with v0.3, `install.sh` detects this situation automatically on
Raspberry Pi 4/5: it patches `/boot/firmware/config.txt`, installs kiosk
packages, and asks you to reboot.  After reboot the display service starts
on its own.

**Manual fix (if needed):**

```bash
# 1. Add KMS overlay to boot config
echo -e '\ndtoverlay=vc4-kms-v3d\nhdmi_force_hotplug=1' | \
    sudo tee -a /boot/firmware/config.txt

# 2. Raise gpu_mem (DietPi sets it to 16 MB — too low for KMS)
sudo sed -i -E 's/^(gpu_mem(_[0-9]+)?=)(8|16)$/\164/' /boot/firmware/config.txt

# 3. Enable audio if disabled
sudo sed -i 's/dtparam=audio=off/dtparam=audio=on/' /boot/firmware/config.txt

# 4. Reboot
sudo reboot

# 5. After reboot — verify DRM is active
ls /sys/class/drm/
# Expected: card0  card1  card1-HDMI-A-1  card1-HDMI-A-2  ...

# 6. Install kiosk packages
sudo apt-get install -y cage cog wtype seatd

# 7. Enable seatd and re-run systemd setup
sudo systemctl enable --now seatd
cd /opt/selena-core && sudo bash scripts/install-systemd.sh
```

**What changes in `/boot/firmware/config.txt`:**

| Setting | DietPi default | Required |
|---------|---------------|----------|
| `dtoverlay=vc4-kms-v3d` | absent | **added** — enables KMS video driver |
| `hdmi_force_hotplug=1` | commented out | **added** — ensures HDMI is detected |
| `gpu_mem_*` | `16` | **64** — minimum for KMS |
| `dtparam=audio` | `off` | **on** — enables onboard audio |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **DietPi: no display, headless detected** | KMS overlay missing — see "DietPi / Minimal Distros" section above |
| **Screen blank after boot** | Check `systemctl status selena-display` and `journalctl -u selena-display` |
| **Chromium not starting** | Verify Xorg is installed: `which Xorg` and check `/tmp/.xinitrc-kiosk` |
| **No audio in container** | PulseAudio may not be running yet — restart container: `docker compose restart core` |
| **cage: "Found 0 GPUs"** | Jetson Tegra DRM is incompatible with cage/wlroots — use Xorg kiosk instead |
| **cog: blank screen, no GPU** | Set `WLR_RENDERER=pixman` and `LIBGL_ALWAYS_SOFTWARE=1` in selena-display.service |
| **Force Chromium over cog** | Set `SELENA_KIOSK_BROWSER=chromium` in selena-display.service environment |
| **Remove cog entirely** | `sudo apt remove cog` → `sudo systemctl restart selena-display.service` |
| **getty restart loop** | Check `/run/user/1000` ownership: `stat -c '%U' /run/user/1000` — must match your user |
| **Touch not working** | Add user to `input` group: `sudo usermod -aG input <user>` |
| **Cursor visible** | Install `unclutter`: `sudo apt install unclutter` |
