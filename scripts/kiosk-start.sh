#!/bin/bash
# SelenaCore Kiosk — Xorg + Chromium (Jetson Tegra)
# Launched from autologin getty on tty1 via .bash_profile

# Wait for SelenaCore to be ready
for i in $(seq 1 30); do
    curl -sf http://localhost:7070/api/v1/health >/dev/null 2>&1 && break
    echo "[kiosk] waiting for SelenaCore... ($i)"
    sleep 2
done

# Create .xinitrc for this session
cat > /tmp/.xinitrc-kiosk <<'XINITRC'
# Disable screen blanking / DPMS
xset s off
xset -dpms
xset s noblank

# Hide cursor after 3 seconds of inactivity
unclutter -idle 3 -root &

# Launch Chromium in kiosk mode
exec chromium-browser \
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
    --user-data-dir=/tmp/chromium-kiosk \
    --disable-popup-blocking \
    --disable-prompt-on-repost \
    --disable-hang-monitor \
    --disable-gpu-sandbox \
    --ignore-gpu-blocklist \
    --enable-gpu-rasterization \
    "http://localhost?kiosk=1"
XINITRC

# Start Xorg + Chromium on current VT
exec xinit /tmp/.xinitrc-kiosk -- :0 vt1 -nocursor -nolisten tcp
