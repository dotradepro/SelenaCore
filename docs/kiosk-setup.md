# SelenaCore — Kiosk & Display Setup

Руководство по настройке автозапуска интерфейса SelenaCore на физическом экране.
Поддерживаемые платформы: **Jetson Orin**, Raspberry Pi 4/5, любой Linux SBC с Ubuntu 22.04+.

---

## Быстрый старт (автоматически)

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
sudo bash scripts/setup.sh
```

Скрипт сделает всё сам: установит зависимости, соберёт образы, настроит сервис.
После завершения — выйдите и войдите снова (обновление групп), kiosk запустится автоматически.

---

## Что происходит при запуске

```
systemd
  └─ selena-display.service
       └─ scripts/start-display.sh
            ├─ Ждёт core-контейнер (healthy)
            ├─ Ждёт UI (http://localhost)
            ├─ Определяет режим дисплея:
            │     desktop  → Chromium --kiosk через DE (GNOME/KDE)
            │     kiosk    → cage + Chromium (Wayland, без DE)
            │     tty      → Python TUI с QR-кодом
            └─ Запускает выбранный режим
```

### Режимы отображения

| Режим | Условие | Описание |
|-------|---------|----------|
| `desktop` | `$DISPLAY` или `$WAYLAND_DISPLAY` установлены | DE запущен (GNOME, KDE и т.д.) — Chromium открывается в нём |
| `kiosk` | cage + chromium + `/dev/dri/card*` | Wayland-compositor без DE, прямой вывод на экран |
| `tty` | fallback | Текстовый интерфейс с QR-кодом для подключения |

---

## Ручная установка (шаг за шагом)

### 1. Системные зависимости

```bash
# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker

# Kiosk (Wayland без DE)
sudo apt install -y cage chromium-browser seatd

# Seat-менеджер
sudo systemctl enable --now seatd
```

### 2. Группы пользователя

```bash
sudo usermod -aG docker,_seatd,video,input,render $USER
```

> Изменения вступают в силу после выхода/входа в систему.

### 3. Директория логов

```bash
sudo mkdir -p /var/log/selena
sudo chown $USER:$USER /var/log/selena
```

### 4. Конфигурация

```bash
cp .env.example .env
# Отредактируйте .env при необходимости
nano .env
```

### 5. Сборка и запуск контейнеров

```bash
docker compose build
docker compose up -d
```

### 6. Установка сервиса дисплея

```bash
sudo bash scripts/setup.sh --no-docker
# или вручную:
sudo bash scripts/install-display.sh
```

---

## Структура сервиса

`/etc/systemd/system/selena-display.service`:

```ini
[Unit]
Description=SelenaCore Kiosk Display
After=network.target docker.service seatd.service
Requires=docker.service
Wants=seatd.service

[Service]
Type=simple
User=YOUR_USER
ExecStart=/path/to/SelenaCore/scripts/start-display.sh
Restart=on-failure
RestartSec=5
Environment=LIBSEAT_BACKEND=seatd
Environment=WLR_BACKENDS=drm,libinput
Environment=WLR_NO_HARDWARE_CURSORS=1

[Install]
WantedBy=multi-user.target
```

---

## Управление сервисом

```bash
# Статус
sudo systemctl status selena-display.service

# Логи в реальном времени
journalctl -u selena-display.service -f

# Перезапуск
sudo systemctl restart selena-display.service

# Остановка
sudo systemctl stop selena-display.service

# Лог файл
tail -f /var/log/selena/display.log
```

---

## Диагностика

### Kiosk не запускается: Permission denied (tty / seat)

```bash
# Проверить группы
id $USER
# Должны быть: _seatd, video, input, render

# Проверить seatd
sudo systemctl status seatd

# Проверить наличие DRI
ls /dev/dri/
```

**Решение**: убедитесь, что пользователь в группах и перелогинились.

### Chromium не найден

```bash
# Проверить
which chromium || which chromium-browser

# Установить
sudo apt install -y chromium-browser
```

На Ubuntu 22.04 `chromium-browser` — это apt-обёртка, которая ставит **snap**-версию Chromium.
Реальный бинарник: `/snap/bin/chromium`.

### cage: failed to start a session

```bash
# Проверить backend
LIBSEAT_BACKEND=seatd cage -- echo ok

# Если seatd не работает — попробовать logind
LIBSEAT_BACKEND=logind cage -- echo ok
```

### Логи при ошибке запуска

```bash
journalctl -u selena-display.service --no-pager -n 50
journalctl -u seatd --no-pager -n 20
```

---

## Jetson Orin — особенности

| Особенность | Решение |
|-------------|---------|
| ARM64 (aarch64) | Все пакеты устанавливаются из `ports.ubuntu.com` |
| NVIDIA Tegra DRM | `/dev/dri/card0` (Tegra), `/dev/dri/card1` (GPU) — cage использует card0 |
| `WLR_NO_HARDWARE_CURSORS=1` | Обязательно — программный курсор для совместимости |
| snap-Chromium | `/snap/bin/chromium` — `find_chromium()` определяет автоматически |
| seatd vs logind | seatd предпочтительнее для системных сервисов без сессии logind |

---

## Raspberry Pi — особенности

| Особенность | Решение |
|-------------|---------|
| Без DE (lite-образ) | Работает kiosk-режим через cage |
| С DE (Raspberry Pi OS Desktop) | Работает desktop-режим, `$DISPLAY=:0` |
| Framebuffer `/dev/fb0` | cage определяет его как fallback DRI |

---

## FAQ

**Q: Можно ли использовать другой браузер?**
A: Теоретически — любой Wayland-браузер. Замените `chromium` в `find_chromium()`.

**Q: Как изменить URL интерфейса?**
A: В сервисе: `Environment=SELENA_UI_URL=http://your-ip:port`

**Q: Как отключить kiosk и вернуть обычный рабочий стол?**
```bash
sudo systemctl disable selena-display.service
sudo systemctl stop selena-display.service
```

**Q: Как обновить проект?**
```bash
git pull
docker compose build
docker compose up -d
```
