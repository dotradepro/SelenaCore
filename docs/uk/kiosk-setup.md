# Конфігурація дисплея та режиму кіоску

## Огляд

SelenaCore підтримує три режими дисплея залежно від обладнання:

| Режим | Коли | Як |
|-------|------|-----|
| **Кіоск (Xorg)** | Headless + HDMI-екран (Jetson/RPi) | getty autologin → xinit → Chromium |
| **Вікно робочого столу** | GNOME/KDE запущено | Вікно Chromium у режимі кіоску |
| **TUI** | Дисплей відсутній | Python TUI з QR-кодом |

**Рекомендовано для production:** Headless кіоск (без робочого столу). Економить ~1 ГБ RAM.

---

## Налаштування Headless-кіоску (Рекомендовано)

Це production-конфігурація для Jetson та Raspberry Pi. GNOME/GDM3 вимкнені, Chromium запускається напряму через Xorg та `xinit`.

### 1. Вимкнути робоче середовище

```bash
# Переключити на headless-завантаження
sudo systemctl disable gdm3
sudo systemctl set-default multi-user.target

# Вимкнути непотрібні сервіси
sudo systemctl mask update-manager.service
# Опційно: sudo systemctl disable cups cups-browsed ModemManager

# Перезавантажити
sudo reboot

# Перевірити
systemctl get-default   # → multi-user.target
```

### 2. Виправити Runtime Directory (Постійно)

Створити `/etc/tmpfiles.d/fix-runtime-dir.conf`:

```ini
d /run/user/1000 0700 <ваш-користувач> <ваш-користувач> -
```

Застосувати:

```bash
sudo cp setup/fix-runtime-dir.conf /etc/tmpfiles.d/
sudo systemd-tmpfiles --create /etc/tmpfiles.d/fix-runtime-dir.conf
```

### 3. Налаштувати Getty Autologin на TTY1

Створити override `/etc/systemd/system/getty@tty1.service.d/override.conf`:

```ini
[Service]
ExecStartPre=-/bin/bash -c 'mkdir -p /run/user/1000 && chown <user>:<user> /run/user/1000 && chmod 700 /run/user/1000'
ExecStart=
ExecStart=-/sbin/agetty --autologin <ваш-користувач> --noclear %I $TERM
```

Встановити:

```bash
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d/
sudo cp setup/getty-autologin-override.conf /etc/systemd/system/getty@tty1.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart getty@tty1.service
```

### 4. Скрипт запуску кіоску

Файл `scripts/kiosk-start.sh` запускається автоматично з `~/.bash_profile` на tty1:

```bash
# ~/.bash_profile
if [ -f "$HOME/.profile" ]; then
    . "$HOME/.profile"
fi

# Автозапуск кіоску тільки на tty1
if [ "$(tty)" = "/dev/tty1" ]; then
    exec /path/to/SelenaCore/scripts/kiosk-start.sh
fi
```

Скрипт:
1. Чекає готовності SelenaCore API (до 60 секунд)
2. Створює тимчасовий `.xinitrc` (вимикає затемнення екрану, ховає курсор)
3. Запускає `xinit` з Chromium у режимі кіоску на `vt1`

### 5. PulseAudio для голосу

У headless-режимі PulseAudio запускається автоматично через сесію користувача (`systemd --user`). Docker-контейнер отримує доступ до PulseAudio через volume mount:

```yaml
# docker-compose.yml
volumes:
  - /run/user/1000/pulse:/run/user/1000/pulse:rw
  - ~/.config/pulse/cookie:/root/.config/pulse/cookie:ro
environment:
  - PULSE_SERVER=unix:/run/user/1000/pulse/native
```

**Важливо:** Якщо контейнер запустився раніше PulseAudio, перезапустіть його:

```bash
docker compose restart core
```

---

## Послідовність завантаження

```
systemd (multi-user.target)
  ├── getty@tty1 (autologin)
  │     └── .bash_profile
  │           └── kiosk-start.sh
  │                 ├── очікування API health
  │                 └── xinit → Xorg + Chromium кіоск
  ├── docker (контейнер selena-core)
  │     └── FastAPI :80 (єдиний API + SPA) + TLS-проксі :443
  ├── vosk-server.service
  │     └── Vosk STT (нативно, без контейнера)
  └── pulseaudio (сесія користувача)
        └── аудіо I/O для голосу
```

---

## Примітки для NVIDIA Jetson

- **Wayland (cage) не працює** на Jetson Tegra DRM — використовуйте Xorg
- GPU-драйвер NVIDIA Tegra вимагає Xorg; `wlroots` не може відкрити `/dev/dri/card0`
- Chromium використовує GPU-растеризацію через `--enable-gpu-rasterization`

---

## Порівняння RAM

| Режим | RAM ОС | Доступно для AI |
|-------|--------|-----------------|
| Повний GNOME desktop | ~1.7 ГБ | ~5.7 ГБ |
| **Headless кіоск (Xorg)** | **~0.7 ГБ** | **~6.7 ГБ** |
| Без дисплея (тільки SSH) | ~0.65 ГБ | ~6.75 ГБ |

На 8 ГБ Jetson headless економить ~1 ГБ для моделей Ollama LLM.

---

## Оновлення дисплея кіоску

Після деплою змін фронтенду:

```bash
# З xdotool (Xorg кіоск)
DISPLAY=:0 xdotool key F5

# Повний перезапуск кіоску
sudo systemctl restart getty@tty1.service
```

---

## Повернення до режиму робочого столу

```bash
sudo systemctl set-default graphical.target
sudo systemctl enable gdm3
sudo reboot
```

---

## Режим TUI (без дисплея)

Коли HDMI не підключено і кіоск не налаштований:

- Система завантажується в headless TTY
- Управління повністю через SSH
- QR-код для мобільного налаштування через `tty_status.py`
- `smarthome-display.service` показує статус TUI на tty1

---

## Конфігурація дисплея в core.yaml

```yaml
ui:
  host: "0.0.0.0"
  port: 80
  https: true
```

---

## Усунення несправностей

| Проблема | Рішення |
|----------|---------|
| **Екран порожній після завантаження** | Перевірте `systemctl status getty@tty1` та `journalctl -u getty@tty1` |
| **Chromium не запускається** | Перевірте наявність Xorg: `which Xorg` та `/tmp/.xinitrc-kiosk` |
| **Немає аудіо в контейнері** | PulseAudio може не працювати — перезапустіть: `docker compose restart core` |
| **cage: "Found 0 GPUs"** | Jetson Tegra DRM несумісний з cage/wlroots — використовуйте Xorg |
| **getty restart loop** | Перевірте власника `/run/user/1000`: `stat -c '%U' /run/user/1000` |
| **Сенсорний екран не працює** | Додайте користувача в групу `input`: `sudo usermod -aG input <user>` |
| **Курсор видимий** | Встановіть `unclutter`: `sudo apt install unclutter` |
