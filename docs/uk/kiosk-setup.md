# Конфігурація дисплея та режиму кіоску

## Огляд

SelenaCore підтримує чотири режими дисплея залежно від обладнання:

| Режим | Коли | Як |
|-------|------|-----|
| **Кіоск (Wayland/cog)** | Headless + HDMI-екран (RPi/generic) | cage + cog (WPE WebKit), ~50 МБ |
| **Кіоск (Xorg)** | Headless + HDMI-екран (Jetson) | getty autologin → xinit → Chromium |
| **Вікно робочого столу** | GNOME/KDE запущено | Вікно Chromium у режимі кіоску |
| **TUI** | Дисплей відсутній | Python TUI з QR-кодом |

**Рекомендовано для production:** Headless кіоск з cog (WPE WebKit). Економить ~1 ГБ RAM порівняно з desktop та ~250 МБ порівняно з Chromium кіоском.

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

### 4. Запуск кіоску

Кіоск-дисплей тепер встановлюється автоматично майстром (крок провижінінга
`install_native_services`) через [scripts/install-systemd.sh](../../scripts/install-systemd.sh).
Скрипт визначає наявність `cage` та підключеного DRM-виходу і генерує
`selena-display.service`, що вказує на [scripts/start-display.sh](../../scripts/start-display.sh),
який запускає `cage + cog` (WPE WebKit) у kiosk-режимі на активному VT. Якщо
`cog` не встановлений, використовується `cage + chromium` як fallback. Можна
примусово вибрати браузер через змінну оточення `SELENA_KIOSK_BROWSER`.

Ніяких ручних модифікацій `~/.bash_profile` чи autologin agetty більше не
потрібно.

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
  ├── selena-display.service
  │     └── start-display.sh
  │           ├── kiosk: cage → cog (WPE WebKit, пріоритет) або Chromium
  │           ├── desktop: вікно Chromium кіоск у існуючому DE
  │           └── tty: Python TUI з QR-кодом
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
- cog (WPE WebKit) має ту саму обмеженість Tegra DRM — він працює всередині cage
- Chromium використовує GPU-растеризацію через `--enable-gpu-rasterization`
- На Jetson використовуйте `kiosk-start.sh` (шлях Xorg) або desktop режим

---

## Порівняння RAM

| Режим | RAM ОС | Доступно для AI |
|-------|--------|-----------------|
| Повний GNOME desktop | ~1.7 ГБ | ~5.7 ГБ |
| Headless кіоск (Chromium/Xorg) | ~0.7 ГБ | ~6.7 ГБ |
| **Headless кіоск (cog/Wayland)** | **~0.5 ГБ** | **~7.0 ГБ** |
| Без дисплея (тільки SSH) | ~0.65 ГБ | ~6.75 ГБ |

На 8 ГБ Jetson headless економить ~1 ГБ для моделей Ollama LLM.

---

## Оновлення дисплея кіоску

Після деплою змін фронтенду:

```bash
# З wtype (Wayland кіоск — працює з cog та Chromium)
sudo XDG_RUNTIME_DIR=/run/user/0 WAYLAND_DISPLAY=wayland-0 wtype -k F5

# З xdotool (Xorg кіоск — Jetson)
DISPLAY=:0 xdotool key F5

# Повний перезапуск кіоску
sudo systemctl restart selena-display.service
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
- TUI-статус можна запустити вручну:
  `docker compose exec core python -m system_modules.ui_core.tty_status`

---

## Конфігурація дисплея в core.yaml

```yaml
ui:
  host: "0.0.0.0"
  port: 80
  https: true
```

---

## DietPi / Мінімальні дистрибутиви — KMS не увімкнено

Деякі мінімальні дистрибутиви (DietPi, Armbian minimal, кастомні образи)
поставляються з **вимкненим** KMS-відеодрайвером. Без KMS ядро не створює
`/sys/class/drm/`, тому `install.sh` не бачить дисплей і переходить у
режим **headless** — навіть якщо HDMI-екран фізично підключено до
Raspberry Pi.

**Симптоми:**
- `install.sh` повідомляє `Display=false Headless=true` на Pi з екраном
- `cage`, `cog`, `wtype` не встановлені
- `selena-display.service` не створено
- `/sys/class/drm/` не існує

**Автоматичне виправлення (install.sh ≥ 0.3):**

Починаючи з v0.3, `install.sh` автоматично виявляє цю ситуацію на
Raspberry Pi 4/5: патчить `/boot/firmware/config.txt`, встановлює
kiosk-пакети і просить перезавантажити. Після reboot display service
запускається автоматично.

**Ручне виправлення (за потреби):**

```bash
# 1. Додати KMS overlay до boot config
echo -e '\ndtoverlay=vc4-kms-v3d\nhdmi_force_hotplug=1' | \
    sudo tee -a /boot/firmware/config.txt

# 2. Підняти gpu_mem (DietPi ставить 16 МБ — замало для KMS)
sudo sed -i -E 's/^(gpu_mem(_[0-9]+)?=)(8|16)$/\164/' /boot/firmware/config.txt

# 3. Увімкнути аудіо якщо вимкнене
sudo sed -i 's/dtparam=audio=off/dtparam=audio=on/' /boot/firmware/config.txt

# 4. Перезавантажити
sudo reboot

# 5. Після reboot — перевірити DRM
ls /sys/class/drm/
# Очікувано: card0  card1  card1-HDMI-A-1  card1-HDMI-A-2  ...

# 6. Встановити kiosk-пакети
sudo apt-get install -y cage cog wtype seatd

# 7. Увімкнути seatd і перезапустити systemd setup
sudo systemctl enable --now seatd
cd /opt/selena-core && sudo bash scripts/install-systemd.sh
```

**Що змінюється в `/boot/firmware/config.txt`:**

| Параметр | DietPi за замовчуванням | Потрібно |
|----------|----------------------|----------|
| `dtoverlay=vc4-kms-v3d` | відсутній | **додано** — вмикає KMS-відеодрайвер |
| `hdmi_force_hotplug=1` | закоментований | **додано** — гарантує виявлення HDMI |
| `gpu_mem_*` | `16` | **64** — мінімум для KMS |
| `dtparam=audio` | `off` | **on** — вмикає вбудоване аудіо |

---

## Усунення несправностей

| Проблема | Рішення |
|----------|---------|
| **DietPi: немає дисплея, headless** | KMS overlay відсутній — див. секцію "DietPi / Мінімальні дистрибутиви" вище |
| **Екран порожній після завантаження** | Перевірте `systemctl status selena-display` та `journalctl -u selena-display` |
| **Chromium не запускається** | Перевірте наявність Xorg: `which Xorg` та `/tmp/.xinitrc-kiosk` |
| **Немає аудіо в контейнері** | PulseAudio може не працювати — перезапустіть: `docker compose restart core` |
| **cage: "Found 0 GPUs"** | Jetson Tegra DRM несумісний з cage/wlroots — використовуйте Xorg |
| **cog: порожній екран, немає GPU** | Встановіть `WLR_RENDERER=pixman` та `LIBGL_ALWAYS_SOFTWARE=1` в selena-display.service |
| **Примусово Chromium замість cog** | Встановіть `SELENA_KIOSK_BROWSER=chromium` в змінних оточення selena-display.service |
| **Видалити cog повністю** | `sudo apt remove cog` → `sudo systemctl restart selena-display.service` |
| **getty restart loop** | Перевірте власника `/run/user/1000`: `stat -c '%U' /run/user/1000` |
| **Сенсорний екран не працює** | Додайте користувача в групу `input`: `sudo usermod -aG input <user>` |
| **Курсор видимий** | Встановіть `unclutter`: `sudo apt install unclutter` |
