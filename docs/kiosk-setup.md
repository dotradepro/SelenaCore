# Display and Kiosk Mode Configuration

## Auto-Detection Logic

1. Desktop environment (GNOME/KDE) detected → Chromium kiosk window
2. Headless + HDMI screen detected → cage Wayland compositor + Chromium
3. No display → Python TUI with QR code

## Systemd Service

```ini
# selena-display.service
[Unit]
Description=Selena Display
After=smarthome-core.service

[Service]
ExecStart=/usr/bin/cage -- chromium-browser --kiosk http://localhost
Restart=always

[Install]
WantedBy=graphical.target
```

## Cage + Chromium Setup (headless with screen)

```bash
sudo apt install cage chromium-browser
# Service automatically uses cage for Wayland kiosk
```

## TUI Mode (no display)

- Automatically activates when no display detected
- Shows system status in terminal
- Displays QR code for mobile access

## Display Configuration in core.yaml

```yaml
ui:
  host: "0.0.0.0"
  port: 80
  https: true
```

## Troubleshooting

- **Screen blank:** check `selena-display.service` status
- **Wrong display mode:** set `display_mode` in core.yaml manually
- **Touch not working:** check input permissions in cage config
