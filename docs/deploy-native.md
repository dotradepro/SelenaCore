# Native deployment — multi-distro guide

This guide covers running SelenaCore on Linux distributions beyond the
primary-tested Debian/Ubuntu family (Raspberry Pi OS, Jetson L4T, Debian 12,
Ubuntu 22.04/24.04). The installer now auto-detects the package manager and
provides best-effort support for Fedora/RHEL, Arch, and openSUSE.

> **Tested on:** Jetson L4T, Raspberry Pi OS, Debian 12, Ubuntu 22.04/24.04.
>
> **Community-supported (best-effort):** Fedora 40+, RHEL 9 / Rocky / Alma,
> Arch Linux / Manjaro, openSUSE Tumbleweed / Leap 15.5+.
>
> Report issues: <https://github.com/dotradepro/SelenaCore/issues>

## Requirements (all distros)

- systemd (SelenaCore uses systemd units for `smarthome-core`, `smarthome-agent`,
  `piper-tts`, `selena-display`). Non-systemd init systems (OpenRC on Alpine,
  runit on Void) are not supported — the wizard will skip native-service
  installation and print instructions.
- 64-bit CPU (amd64 / arm64). armv7 Raspberry Pi 3 is **not** supported.
- 4 GB RAM minimum (8 GB recommended for local LLM).
- Docker Engine 24+ with the Compose plugin.
- Python 3.9+ on the host (the installer auto-installs a newer one via `uv`
  if the system interpreter is too old).
- NVIDIA GPU owners: NVIDIA drivers + Container Toolkit (installer handles
  this on apt/dnf/pacman/zypper).

## One-command install

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
sudo ./install.sh
```

The installer:

1. Detects the package manager (`apt` / `dnf` / `pacman` / `zypper`).
2. Installs base packages (curl, git, Python, ffmpeg, arp-scan, NetworkManager,
   build toolchain, …).
3. Installs Docker Engine + Compose plugin.
4. Sets up the `selena` system user and directory layout.
5. Starts the docker stack (`core`, `agent`) on port 80.
6. Prints `http://<lan-ip>/` — open it in a browser to finish via the wizard.

Note: Piper TTS now runs inside the `smarthome-core` container; the
host is no longer seeded with a `piper-tts.service` systemd unit. Ollama
is user-managed — install it yourself if you want a local LLM (see the
next section) or point the wizard's **LLM Provider** step at a cloud
provider / remote Ollama instance.

The wizard then downloads STT/TTS models, creates the admin user,
registers the device with the platform, and enables the systemd services.

## Session user & kiosk binding

SelenaCore runs two classes of services:

| Service | Runs as | Why |
|---|---|---|
| `smarthome-core.service` (Docker container) | `selena` system user | Background daemon — no seat, no home dir needed |
| `selena-display.service` (cage kiosk) | **operator user** | Wayland compositor needs a login seat, `root` has none |
| `piper-tts.service` (optional) | **operator user** | Stores voice models under `~/.local/share/piper/` |

"Operator user" = the human who sits in front of the device (or, for
headless installs, any existing non-root user). By default `install.sh`
uses `$SUDO_USER` — the user who ran `sudo ./install.sh`.

### Picking the right user

```bash
# Case 1 — logged in as the operator, use sudo:
pi@raspberrypi:~ $ sudo ./install.sh
# → binds kiosk/piper to `pi`, adds `pi` to docker+selena groups

# Case 2 — SSH as root (no sudo), or any other case where $SUDO_USER is
# empty/root, you MUST pass --kiosk-user:
root@box:~ # ./install.sh --kiosk-user=alice
# → binds kiosk/piper to `alice`

# Case 3 — install.sh refuses to bind kiosk to root automatically.
# If no --kiosk-user is passed and $SUDO_USER is empty/root, the
# selena-display.service setup is SKIPPED with a warning. Everything
# else installs normally — you can enable the kiosk later by re-running
# install.sh with --kiosk-user=NAME.
```

### Changing the operator later

Re-run `sudo ./install.sh` from the new user's session, or pass
`--kiosk-user=<newname>`. The `selena-display.service` and
`piper-tts.service` units get regenerated against the new user; the old
user stays in `docker` and `selena` groups (harmless). No uninstall
needed.

### What about the `selena` system user?

Created automatically by `install.sh` — a system user (no shell, no
login) that owns the docker container, `/var/lib/selena`, and `/secure`.
You never log in as `selena`. Human operators get **added to its group**
so they can read/write shared log and model directories.

## Per-distro notes

### Debian / Ubuntu / Raspberry Pi OS / Jetson L4T

No extra steps. This is the primary tested path.

### Fedora / RHEL / Rocky / Alma (dnf)

```bash
sudo dnf install -y git curl
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
sudo ./install.sh
```

Notes:
- Docker is installed via `https://get.docker.com` which configures the
  `docker-ce` repo and pulls the Compose plugin.
- NVIDIA Container Toolkit is installed from
  `nvidia.github.io/libnvidia-container/stable/rpm/`.
- SELinux: if enabled in enforcing mode, you may need to relabel
  `/var/lib/selena` and `/secure`:
  ```bash
  sudo chcon -R -t container_file_t /var/lib/selena /secure
  ```
- Firewall: `firewalld` is on by default. Open port 80:
  ```bash
  sudo firewall-cmd --permanent --add-service=http && sudo firewall-cmd --reload
  ```

### Arch Linux / Manjaro (pacman)

```bash
sudo pacman -Syu --needed git curl
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
sudo ./install.sh
```

Notes:
- Docker and NVIDIA Container Toolkit come from official Arch repos
  (`community`). If `nvidia-container-toolkit` is missing, install from AUR.
- `cog` and `seatd` are not installed automatically on Arch — only `cage` +
  `wtype`. Kiosk mode works; `selena-display.service` will use `cage` directly.

### openSUSE Tumbleweed / Leap (zypper)

```bash
sudo zypper install -y git curl
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
sudo ./install.sh
```

Notes:
- Docker installed via `https://get.docker.com`.
- NVIDIA toolkit repo added via `zypper addrepo` from the NVIDIA RPM mirror.
- AppArmor/SELinux are typically off; no extra steps.

## Manual install (no `install.sh`)

If you prefer to do it by hand:

```bash
# 1. Install base packages (adapt to your distro)
#    See install.sh `install_host_packages` for the full list.

# 2. Install Docker + Compose
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

# 3. (Optional) Install Ollama yourself if you want a local LLM.
#    Selena no longer installs or manages Ollama — pick a URL, point the
#    wizard's "LLM Provider" step at it. Use a cloud provider instead
#    (OpenAI / Anthropic / Groq / Google) and skip this step entirely.
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
ollama pull llama3.2   # or whichever model fits your hardware

# 4. Create directories (Piper / Vosk — Piper runs inside the container
#    now; Vosk models still volume-mounted from the host)
sudo install -d -m 0755 /var/lib/selena/models/{piper,vosk,whisper} \
    /var/lib/selena/speaker_embeddings /var/log/selena
sudo install -d -m 0750 /secure

# 5. Clone and configure
git clone https://github.com/dotradepro/SelenaCore.git /opt/selena-core
cd /opt/selena-core
cp config/core.yaml.example config/core.yaml
cp .env.example .env

# 6. Build frontend + start stack (add --build-arg TARGET=jetson on Jetson)
npx vite build
docker compose build --build-arg TARGET=cpu core
docker compose up -d

# 7. Stage systemd units (wizard will enable them)
sudo bash scripts/install-systemd.sh

# 8. Open http://<ip>/ and run the wizard
```

## Troubleshooting

### Installer exits with "PKG not found"

You're on a distro with none of apt/dnf/pacman/zypper. SelenaCore does not
support nixpkgs, xbps (Void), apk (Alpine), or eopkg (Solus) out of the box.
Use the **Manual install** section above, adapting package names by hand.

### Ollama does not start

Ollama is user-managed — Selena only talks to it over HTTP. Check the
service you installed:

```bash
systemctl status ollama
journalctl -u ollama -n 50
```

Most common cause: GPU driver missing or port 11434 taken. Override with
`OLLAMA_HOST=127.0.0.1:11435` in `/etc/systemd/system/ollama.service.d/override.conf`
and set `voice.providers.ollama.url: http://127.0.0.1:11435` in
`config/core.yaml` (or enter it in the wizard's LLM Provider step).

### Piper TTS is unhealthy

Piper now runs inside `smarthome-core` as a subprocess on `:5100`. Check
the container log:
```bash
docker compose logs core | grep -i piper
curl -s http://localhost:5100/health
```
If the log says the voice `.onnx` is missing, download one via the
wizard's TTS step or drop a Piper voice pair into
`/var/lib/selena/models/piper/` and restart the container.
Upgrading from the old host-side `piper-tts.service`? Run
`sudo bash scripts/migrate_piper_to_container.sh` once.

### Container cannot access PulseAudio

The docker-compose.yml mounts `/run/user/${HOST_UID}/pulse`. If your session
is rootless / headless / lingering-disabled, the socket may be absent.
Enable linger:
```bash
sudo loginctl enable-linger "$USER"
```
Then re-create the compose stack:
```bash
docker compose down && docker compose up -d
```

### arp-scan returns empty list

`arp-scan` needs raw-socket capability. The compose runs the container with
`network_mode: host` + `privileged: true`, which is required. On non-Debian
distros, verify the container inherits these flags:
```bash
docker inspect selena-core | grep -E 'NetworkMode|Privileged'
```

### SELinux blocks the container (Fedora/RHEL)

```bash
sudo setenforce 0   # verify
# If that fixes it, relabel the install paths:
sudo chcon -R -t container_file_t /var/lib/selena /secure /opt/selena-core
sudo setenforce 1
```

## Contributing non-Debian testing feedback

If you run SelenaCore on a distro outside the tested set, we'd love a report.
Open an issue with:

- Distro + version (`cat /etc/os-release`)
- `sudo ./install.sh --dry-run` output
- First error or warning
- Output of `docker compose logs core --tail 50`

See [CONTRIBUTING.md](../.github/CONTRIBUTING.md) for details.
