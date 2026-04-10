#!/bin/bash
# SelenaCore Kiosk — Xorg + Chromium (Jetson Tegra)
# Launched from autologin getty on tty1 via .bash_profile

# Wait for SelenaCore to be ready
for i in $(seq 1 30); do
    curl -sf http://localhost/api/v1/health >/dev/null 2>&1 && break
    echo "[kiosk] waiting for SelenaCore... ($i)"
    sleep 2
done

# Ensure XDG_RUNTIME_DIR for systemd --user
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# Create .xinitrc for this session
cat > /tmp/.xinitrc-kiosk <<XINITRC
#!/bin/sh
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin"
export XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"

# Log for debugging
exec >>/tmp/xinitrc-kiosk.log 2>&1
echo "=== \$(date) ==="
echo "PATH=\$PATH"
echo "XDG_RUNTIME_DIR=\$XDG_RUNTIME_DIR"
echo "DBUS=\$DBUS_SESSION_BUS_ADDRESS"
echo "DISPLAY=\$DISPLAY"

# Disable screen blanking / DPMS
xset s off
xset -dpms
xset s noblank

# Hide cursor after 3 seconds of inactivity
command -v unclutter >/dev/null 2>&1 && unclutter -idle 3 -root &

# Chromium restart loop — keeps kiosk alive if it crashes
while true; do
    systemd-run --user --scope -p "Delegate=yes" -- /snap/bin/chromium \
        --kiosk \
        --no-sandbox \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --disable-translate \
        --no-first-run \
        --disable-features=Translate,TranslateUI,ChromeTranslatePopup \
        --check-for-update-interval=31536000 \
        --autoplay-policy=no-user-gesture-required \
        --disable-pinch \
        --overscroll-history-navigation=0 \
        --no-default-browser-check \
        --disable-component-update \
        --disable-background-networking \
        --disable-sync \
        --start-fullscreen \
        --user-data-dir=\$HOME/.chromium-kiosk \
        --disable-popup-blocking \
        --disable-prompt-on-repost \
        --disable-hang-monitor \
        --disable-gpu-sandbox \
        --disable-gpu \
        "http://localhost?kiosk=1" || true
    sleep 3
done
XINITRC

# Start Xorg + Chromium on current VT
exec xinit /tmp/.xinitrc-kiosk -- :0 vt1 -nocursor -nolisten tcp
