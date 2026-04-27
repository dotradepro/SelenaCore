#!/usr/bin/env bash
# install-snapcast.sh — install snapserver and wire a pcm_s16le/48000 FIFO source.
#
# Idempotent. Does NOT start the service — run `sudo systemctl enable --now
# snapserver` when your ESP32 firmware bundles snapclient and you're ready
# to route media_player output through it. See _private/snapcast_integration.md.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "This script must run as root (sudo bash $0)" >&2
    exit 1
fi

FIFO=/tmp/snapcast_fifo
CONF=/etc/snapserver.conf

echo "→ apt install snapserver..."
if ! dpkg -s snapserver >/dev/null 2>&1; then
    apt-get update
    apt-get install -y snapserver
else
    echo "  snapserver already installed"
fi

echo "→ creating FIFO at $FIFO..."
if [[ ! -p $FIFO ]]; then
    mkfifo "$FIFO"
    # 'snapserver' user created by the package reads the FIFO
    chown snapserver:audio "$FIFO" 2>/dev/null || chown snapserver "$FIFO" 2>/dev/null || true
    chmod 660 "$FIFO"
else
    echo "  FIFO already exists"
fi

echo "→ writing $CONF..."
# Preserve the original once, then write a minimal config we own.
if [[ -f "$CONF" && ! -f "$CONF.selena-bak" ]]; then
    cp "$CONF" "$CONF.selena-bak"
fi

cat > "$CONF" <<'EOF'
# /etc/snapserver.conf — managed by SelenaCore install-snapcast.sh
# Edit freely; the installer won't overwrite if $CONF.selena-bak already exists
# on re-runs. See _private/snapcast_integration.md for wiring details.

[server]
threads = -1
pidfile = /var/run/snapserver/pid
user = snapserver
group = snapserver

[http]
enabled = true
bind_to_address = 0.0.0.0
port = 1780

[tcp]
enabled = true
bind_to_address = 0.0.0.0
port = 1705

[stream]
bind_to_address = 0.0.0.0
port = 1704
# pcm_s16le / 48000 Hz / stereo — matches what media_player's future
# FIFO sink will write. Piper (22050 Hz mono) is NOT piped through this
# — TTS stays on the WebSocket TTS_CHUNK path.
source = pipe:///tmp/snapcast_fifo?name=selena&sampleformat=48000:16:2&codec=pcm

buffer = 1000
chunk_ms = 20
sampleformat = 48000:16:2
codec = pcm
EOF

echo "→ reload systemd (so the package unit picks up the config)..."
systemctl daemon-reload

cat <<'EOF'

Snapcast server installed and configured, but NOT started.

Next steps:
  1. Flash satellite firmware with snapclient support (see
     _private/snapcast_integration.md).
  2. When ready:  sudo systemctl enable --now snapserver
  3. Smoke test from the hub:
        ffmpeg -re -stream_loop -1 -i /path/to/song.mp3 \
               -f s16le -ar 48000 -ac 2 /tmp/snapcast_fifo
  4. Open http://<hub-ip>:1780 to see connected clients + per-group volume.

Until the hub-side FIFO sink is implemented in media_player, nothing in
SelenaCore writes to /tmp/snapcast_fifo automatically. That wiring is
tracked in _private/snapcast_integration.md.
EOF
