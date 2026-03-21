# Развёртывание SelenaCore на Raspberry Pi

## Поддерживаемые платформы

| Устройство | Поддержка | Рекомендуется |
|-----------|-----------|---------------|
| Raspberry Pi 5 (8 GB) | ✅ Полная | Да — включая LLM |
| Raspberry Pi 5 (4 GB) | ✅ Полная | Да |
| Raspberry Pi 4 (4/8 GB) | ✅ Полная | Без LLM |
| Raspberry Pi 4 (2 GB) | ⚠️ Ограниченная | LLM отключён |
| Debian x86-64 / ARM | ✅ | Да |

---

## Подготовка системы

### 1. Операционная система

Рекомендовано: **Raspberry Pi OS 64-bit Lite** (без GUI) или **Debian 12 Bookworm**.

```bash
# Обновить систему
sudo apt update && sudo apt upgrade -y

# Обязательные зависимости
sudo apt install -y \
    python3.11 python3.11-venv python3-pip \
    git curl wget \
    sqlite3 \
    ffmpeg \
    alsa-utils pulseaudio \
    iptables iptables-persistent \
    avahi-daemon avahi-utils \
    bluetooth bluez bluez-tools
```

### 2. Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Перелогиниться или: newgrp docker

# Проверить
docker run --rm hello-world
```

### 3. Клонировать репозиторий

```bash
sudo mkdir -p /opt/selena-core
sudo chown $USER:$USER /opt/selena-core
cd /opt/selena-core

git clone https://github.com/dotradepro/SelenaCore.git .
```

---

## Настройка

### 4. Конфигурация .env

```bash
cp .env.example .env
nano .env
```

Минимальные настройки:

```bash
CORE_PORT=7070
UI_PORT=8080
CORE_DATA_DIR=/var/lib/selena
CORE_SECURE_DIR=/secure
CORE_LOG_LEVEL=INFO
DEBUG=false

# Для платформенного подключения (опционально):
PLATFORM_API_URL=https://smarthome-lk.com/api/v1
PLATFORM_DEVICE_HASH=           # заполняется при регистрации на платформе
```

### 5. Создать директории

```bash
sudo mkdir -p /var/lib/selena
sudo mkdir -p /secure/tokens
sudo mkdir -p /secure/tls
sudo mkdir -p /secure/core_backup
sudo mkdir -p /var/log/selena

sudo chown -R $USER:$USER /var/lib/selena /secure /var/log/selena
sudo chmod 700 /secure
```

### 6. Инициализация хранилища

```bash
cd /opt/selena-core

# Создать core.manifest (SHA256 всех файлов ядра)
python3 agent/manifest.py --init

# Сгенерировать HTTPS сертификат
python3 scripts/generate_https_cert.py
```

---

## Запуск

### Docker Compose (рекомендовано)

```bash
cd /opt/selena-core
docker compose up -d

# Проверить логи
docker compose logs -f core
docker compose logs -f agent

# Статус
curl http://localhost:7070/api/v1/health
```

### Systemd (если не используешь Docker)

```bash
# Установить сервисы
sudo cp smarthome-core.service /etc/systemd/system/
sudo cp smarthome-agent.service /etc/systemd/system/
sudo cp smarthome-modules.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable smarthome-core smarthome-agent smarthome-modules
sudo systemctl start smarthome-core

# Проверить
sudo systemctl status smarthome-core
journalctl -u smarthome-core -f
```

---

## Onboarding Wizard

После первого запуска:

1. **С монитором:** браузер откроется автоматически → `http://localhost:8080`
2. **Без монитора (headless):**
   - Если есть Wi-Fi — ядро поднимает точку доступа `SmartHome-Setup` / пароль `smarthome`
   - Подключись с телефона → открой `192.168.4.1`

### Шаги Wizard

| Шаг | Описание |
|-----|----------|
| `wifi` | Подключиться к Wi-Fi |
| `language` | Язык интерфейса (`ru`, `uk`, `en`) |
| `device_name` | Имя устройства |
| `timezone` | Часовой пояс |
| `stt_model` | Выбор модели распознавания речи |
| `tts_voice` | Выбор голоса синтеза речи |
| `admin_user` | Создать аккаунт администратора |
| `platform` | Подключение к платформе SmartHome LK (опционально) |
| `import` | Импорт устройств (Home Assistant, Tuya, Philips Hue) |

---

## Аудио

### Автодетект

Ядро автоматически определяет доступные устройства. Проверить:

```bash
# Список карт ALSA
arecord -l    # входы
aplay -l      # выходы

# USB микрофон должен появиться как card 1
```

### I2S микрофон (INMP441)

```bash
# Добавить в /boot/config.txt
echo "dtoverlay=googlevoicehat-soundcard" | sudo tee -a /boot/config.txt
sudo reboot

# Проверить после перезагрузки
arecord -l
```

### Bluetooth колонка

```bash
# Через API (рекомендовано)
curl -X POST http://localhost:7070/api/v1/system/bluetooth/pair \
  -H "Authorization: Bearer <token>" \
  -d '{"mac": "AA:BB:CC:DD:EE:FF"}'

# Или вручную
bluetoothctl
  power on
  scan on
  pair AA:BB:CC:DD:EE:FF
  trust AA:BB:CC:DD:EE:FF
  connect AA:BB:CC:DD:EE:FF
  quit
```

### Принудительный выбор аудиоустройства

```bash
# В .env
AUDIO_FORCE_INPUT=hw:2,0
AUDIO_FORCE_OUTPUT=bluez_sink.AA_BB_CC
```

---

## Обновление

```bash
cd /opt/selena-core
git pull origin main

# Пересобрать и перезапустить
docker compose down
docker compose build
docker compose up -d

# Обновить core.manifest после обновления ядра
python3 agent/manifest.py --update
```

---

## Брандмауэр (iptables)

```bash
# Установить правила из скрипта
sudo bash scripts/setup_iptables.sh

# Вручную — основные правила
sudo iptables -A INPUT -i lo -j ACCEPT
sudo iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 8080 -j ACCEPT   # UI (LAN)
sudo iptables -A INPUT -p tcp --dport 7070 -j DROP     # Core API (только localhost)
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT     # SSH

# Сохранить
sudo netfilter-persistent save
```

---

## Бэкап

### Локальный бэкап на USB

```bash
curl -X POST http://localhost:7070/api/v1/backup/local \
  -H "Authorization: Bearer <token>" \
  -d '{"destination": "/media/usb0"}'
```

### Облачный бэкап

Данные шифруются E2E (PBKDF2 + AES-256-GCM) до отправки на платформу:

```bash
curl -X POST http://localhost:7070/api/v1/backup/cloud \
  -H "Authorization: Bearer <token>"
```

---

## Мониторинг

```bash
# Статус системы
curl http://localhost:7070/api/v1/system/info | python3 -m json.tool

# Integrity Agent
curl http://localhost:7070/api/v1/integrity/status | python3 -m json.tool

# Аппаратный мониторинг
curl http://localhost:7070/api/v1/system/hardware | python3 -m json.tool
```

---

## Часто задаваемые вопросы

**Q: Модуль не запускается после установки**
A: Проверь статус: `GET /api/v1/modules/{name}`. Статус `ERROR` → смотри логи Docker: `docker logs selena-module-{name}`.

**Q: Голосовой ассистент не слышит wake-word**
A: Проверь аудио вход: `arecord -d 5 test.wav && aplay test.wav`. Если запись тихая — убедись, что усиление микрофона включено: `alsamixer`.

**Q: SAFE MODE — как выйти**
A: Причина — изменение файлов ядра. Автоматический откат из резервной копии должен сработать. Если нет:
```bash
sudo systemctl stop smarthome-core
python3 agent/manifest.py --restore
sudo systemctl start smarthome-core
```

**Q: Занят ли порт 7070**
A: `sudo lsof -i :7070` — найти и завершить конфликтующий процесс.
