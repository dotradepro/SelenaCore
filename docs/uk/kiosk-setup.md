# Конфігурація дисплея та режиму кіоску

## Логіка автовизначення

1. Виявлено стільничне середовище (GNOME/KDE) → вікно Chromium у режимі кіоску
2. Безголовий режим + виявлено HDMI-екран → Wayland-композитор cage + Chromium
3. Дисплей відсутній → TUI на Python з QR-кодом

## Сервіс systemd

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

## Налаштування Cage + Chromium (безголовий режим з екраном)

```bash
sudo apt install cage chromium-browser
# Service automatically uses cage for Wayland kiosk
```

## Режим TUI (без дисплея)

- Автоматично активується, коли дисплей не виявлено
- Показує статус системи в терміналі
- Відображає QR-код для мобільного доступу

## Конфігурація дисплея в core.yaml

```yaml
ui:
  host: "0.0.0.0"
  port: 80
  https: true
```

## Усунення несправностей

- **Екран порожній:** перевірте статус `selena-display.service`
- **Неправильний режим дисплея:** встановіть `display_mode` у core.yaml вручну
- **Сенсорний екран не працює:** перевірте дозволи вводу в конфігурації cage
