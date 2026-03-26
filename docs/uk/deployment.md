# Розгортання SelenaCore на Raspberry Pi

🇬🇧 [English version](../deployment.md)

## Підтримувані платформи

| Пристрій | Підтримка | Рекомендовано |
|----------|-----------|---------------|
| Raspberry Pi 5 (8 GB) | ✅ Повна | Так — включно з LLM |
| Raspberry Pi 5 (4 GB) | ✅ Повна | Так |
| Raspberry Pi 4 (4/8 GB) | ✅ Повна | Без LLM |
| Raspberry Pi 4 (2 GB) | ⚠️ Обмежена | LLM вимкнено |
| Debian x86-64 / ARM | ✅ | Так |

---

## Підготовка системи

### 1. Операційна система

Рекомендовано: **Raspberry Pi OS 64-bit Lite** (без GUI) або **Debian 12 Bookworm**.

```bash
# Оновити систему
sudo apt update && sudo apt upgrade -y

# Обов'язкові залежності
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
# Перелогінитись або: newgrp docker

# Перевірити
docker run --rm hello-world
```

### 3. Клонувати репозиторій

```bash
sudo mkdir -p /opt/selena-core
sudo chown $USER:$USER /opt/selena-core
cd /opt/selena-core

git clone https://github.com/dotradepro/SelenaCore.git .
```

---

## Налаштування

### 4. Конфігурація .env

```bash
cp .env.example .env
nano .env
```

Мінімальні налаштування:

```bash
CORE_PORT=7070
UI_PORT=80
CORE_DATA_DIR=/var/lib/selena
CORE_SECURE_DIR=/secure
CORE_LOG_LEVEL=INFO
DEBUG=false

# Для підключення до платформи (опціонально):
PLATFORM_API_URL=https://smarthome-lk.com/api/v1
PLATFORM_DEVICE_HASH=           # заповнюється при реєстрації на платформі
```

### 5. Створити директорії

```bash
sudo mkdir -p /var/lib/selena
sudo mkdir -p /secure/tokens
sudo mkdir -p /secure/tls
sudo mkdir -p /secure/core_backup
sudo mkdir -p /var/log/selena

sudo chown -R $USER:$USER /var/lib/selena /secure /var/log/selena
sudo chmod 700 /secure
```

### 6. Ініціалізація сховища

```bash
cd /opt/selena-core

# Створити core.manifest (SHA256 усіх файлів ядра)
python3 agent/manifest.py --init

# Згенерувати HTTPS сертифікат
python3 scripts/generate_https_cert.py
```

---

## Запуск

### Docker Compose (рекомендовано)

```bash
cd /opt/selena-core
docker compose up -d

# Перевірити логи
docker compose logs -f core
docker compose logs -f agent

# Статус
curl http://localhost:7070/api/v1/health
```

### Systemd (без Docker)

```bash
# Встановити сервіси
sudo cp smarthome-core.service /etc/systemd/system/
sudo cp smarthome-agent.service /etc/systemd/system/
sudo cp smarthome-modules.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable smarthome-core smarthome-agent smarthome-modules
sudo systemctl start smarthome-core

# Перевірити
sudo systemctl status smarthome-core
journalctl -u smarthome-core -f
```

---

## Onboarding Wizard

Після першого запуску:

1. **З монітором:** браузер відкриється автоматично → `http://localhost:80`
2. **Без монітора (headless):**
   - Якщо є Wi-Fi — ядро піднімає точку доступу `SmartHome-Setup` / пароль `smarthome`
   - Підключіться з телефону → відкрийте `192.168.4.1`

### Кроки Wizard

| Крок | Опис |
|------|------|
| `wifi` | Підключитися до Wi-Fi |
| `language` | Мова інтерфейсу (`uk`, `en`) |
| `device_name` | Назва пристрою |
| `timezone` | Часовий пояс |
| `stt_model` | Вибір моделі розпізнавання мовлення |
| `tts_voice` | Вибір голосу синтезу мовлення |
| `admin_user` | Створити обліковий запис адміністратора |
| `platform` | Підключення до платформи SmartHome LK (опціонально) |
| `import` | Імпорт пристроїв (Home Assistant, Tuya, Philips Hue) |

---

## Аудіо

### Автодетект

Ядро автоматично визначає доступні пристрої. Перевірити:

```bash
# Список карт ALSA
arecord -l    # входи
aplay -l      # виходи

# USB мікрофон повинен з'явитися як card 1
```

### I2S мікрофон (INMP441)

```bash
# Додати в /boot/config.txt
echo "dtoverlay=googlevoicehat-soundcard" | sudo tee -a /boot/config.txt
sudo reboot

# Перевірити після перезавантаження
arecord -l
```

### Bluetooth колонка

```bash
# Через bluetoothctl (API-endpoint для парування Bluetooth ще не реалізовано)
bluetoothctl
  power on
  scan on
  pair AA:BB:CC:DD:EE:FF
  trust AA:BB:CC:DD:EE:FF
  connect AA:BB:CC:DD:EE:FF
  quit
```

### Примусовий вибір аудіопристрою

```bash
# В .env
AUDIO_FORCE_INPUT=hw:2,0
AUDIO_FORCE_OUTPUT=bluez_sink.AA_BB_CC
```

---

## Оновлення

```bash
cd /opt/selena-core
git pull origin main

# Перезібрати та перезапустити
docker compose down
docker compose build
docker compose up -d

# Оновити core.manifest після оновлення ядра
python3 agent/manifest.py --update
```

---

## Брандмауер (iptables)

```bash
# Застосувати правила зі скрипту
sudo bash scripts/setup_firewall.sh

# Вручну — основні правила
sudo iptables -A INPUT -i lo -j ACCEPT
sudo iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT   # UI (LAN)
sudo iptables -A INPUT -p tcp --dport 7070 -j DROP     # Core API (лише localhost)
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT     # SSH

# Зберегти
sudo netfilter-persistent save
```

---

## Бекап

### Локальний бекап на USB

> **Примітка:** REST-endpoints для бекапу ще не реалізовані. Користуйтесь UI або CLI:

```bash
python3 -m system_modules.backup_manager.local_backup --destination /media/usb0
```

### Хмарний бекап

Дані шифруються E2E (PBKDF2 + AES-256-GCM) перед відправкою на платформу:

> **Примітка:** REST-endpoint хмарного бекапу ще не реалізовано. Налаштуйте хмарний бекап через сторінку налаштувань в UI.

---

## Моніторинг

```bash
# Статус системи
curl http://localhost:7070/api/v1/system/info | python3 -m json.tool

# Integrity Agent
curl http://localhost:7070/api/v1/integrity/status | python3 -m json.tool

# Апаратний моніторинг — через UI API:
curl http://localhost:80/api/ui/modules/hw-monitor/stats | python3 -m json.tool
```

---

## Часті запитання

**Q: Модуль не запускається після встановлення**
A: Перевірте статус: `GET /api/v1/modules/{name}`. Статус `ERROR` → дивіться логи Docker: `docker logs selena-module-{name}`.
