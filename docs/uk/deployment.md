# Посібник з розгортання та встановлення SelenaCore

Цей посібник охоплює апаратні вимоги, встановлення, налаштування та поточну експлуатацію розумного хабу SelenaCore.

---

## Підтримуване обладнання

| Платформа | Примітки |
|-----------|----------|
| Raspberry Pi 4/5 | Рекомендовано 4 ГБ+ оперативної пам'яті |
| NVIDIA Jetson Orin Nano | Підтримка TTS/STT з GPU-прискоренням |
| Будь-який Linux SBC (ARM64 або x86_64) | Протестовано на Ubuntu та дистрибутивах на базі Debian |

**Мінімальні вимоги:**

- **2 ГБ оперативної пам'яті** — достатньо для базової функціональності без локальної LLM
- **4 ГБ+ оперативної пам'яті** — необхідно для повної функціональності, включаючи локальний LLM-інференс на базі Ollama

---

## Вимоги до ОС та програмного забезпечення

- Ubuntu 22.04+ або Raspberry Pi OS (Bookworm)
- Docker 24+ та Docker Compose v2
- Python 3.11+

---

## Встановлення

### Єдиний інсталятор (рекомендовано)

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
sudo ./install.sh
```

`install.sh` — **єдиний** скрипт, який запускає користувач. Він робить мінімум,
щоб система стала доступна в браузері, і друкує URL виду `http://<lan-ip>/`.
Решту встановлення (вибір моделей, завантаження, створення адміністратора,
реєстрація на платформі, нативні systemd-сервіси) ви проходите у **майстрі
першого запуску** з прогрес-баром.

Що робить `install.sh`:

1. Виявляє апаратну платформу (Jetson / Raspberry / CUDA / generic Linux)
2. `apt-get install` пакети хоста (Docker, FFmpeg, arp-scan, pulseaudio, nmcli, …)
3. Створює системного користувача `selena` і додає в групи docker/audio/video
4. Створює `/var/lib/selena/{models,…}`, `/var/log/selena`, `/secure`
5. Сідує Piper-голоси з `~/.local/share/piper/models/` (якщо є)
6. Копіює `config/core.yaml.example` → `config/core.yaml` з `wizard.completed=false`
7. `npx vite build` (фронтенд)
8. `docker compose up -d --build` (selena-core + selena-agent)
9. Стейджить `smarthome-core.service` / `smarthome-agent.service` у `/etc/systemd/system/` (поки не enable — це робить майстер)
10. Друкує банер з URL майстра

`install.sh` НЕ качає Whisper / Vosk / Piper-голоси / Ollama-моделі — це робить
майстер, із прогресом у браузері.

### Ручне налаштування

```bash
# Clone the repository
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore

# Copy and edit the environment file
cp .env.example .env
# Edit .env with your settings (see Environment Variables below)

# Build and start
docker compose build
docker compose up -d
```

---

## Архітектура Docker

Файл `docker-compose.yml` визначає два сервіси.

### selena-core (основний сервіс)

Основний контейнер, що виконує застосунок SelenaCore.

- **Образ:** Збирається з `Dockerfile.core` (базовий: `python:3.11-slim`)
- **Мережевий режим:** `host` (необхідний для доступу до аудіо та пристроїв)
- **Привілейований:** `true` (необхідний для доступу до обладнання)
- **Відкриті порти:**
  - `80` — Єдиний API + веб-інтерфейс (один процес)
  - `443` — HTTPS (TLS-проксі до :80)
- **Томи:**
  - `/var/run/docker.sock` — Docker-сокет для керування контейнерами модулів
  - `selena_data:/var/lib/selena` — база даних, голосові моделі, резервні копії
  - `selena_secure:/secure` — зашифровані токени та ключі
  - `/dev/snd` — ALSA звукові пристрої для аудіо вводу/виводу
  - Директорія моделей Ollama (якщо налаштовано)
- **Перевірка стану:** `GET /api/v1/health` кожні 30 секунд
- **Вбудоване ПЗ:** FFmpeg, PortAudio, VLC, ALSA utils (aplay, arecord, amixer)
- **Зовнішні сервіси (нативно на хості):** Piper TTS (`piper-tts.service`), Ollama

### selena-agent (агент цілісності)

Окремий контейнер, що безперервно моніторить цілісність ядра.

- Виконує SHA256-перевірку хешів файлів ядра кожні 30 секунд
- При порушенні цілісності: зупиняє модулі, надсилає сповіщення, ініціює відкат, переходить у **БЕЗПЕЧНИЙ РЕЖИМ**

### Підтримка GPU (NVIDIA Jetson)

Для GPU-прискорення контейнера:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

### Нативний сервіс Piper TTS

Piper TTS працює нативно на хості (не в Docker) для прямого доступу до GPU.

```bash
# Встановити Piper TTS
pip3 install --user piper-tts aiohttp

# Для GPU на Jetson (JetPack 6, CUDA 12.x):
pip3 install --user onnxruntime-gpu --extra-index-url https://pypi.jetson-ai-lab.io/jp6/cu126
pip3 install --user "numpy<2"
sudo ln -sf /usr/lib/aarch64-linux-gnu/libcudnn.so.9 /usr/lib/aarch64-linux-gnu/libcudnn.so
# Або автоматично: bash scripts/build-onnxruntime-gpu.sh

# Розгорнути systemd-сервіс
sudo cp scripts/piper-tts.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now piper-tts

# Перевірити
curl http://localhost:5100/health
# → "device": "gpu", "cuda_available": true
```

> **Примітка:** PyPI `onnxruntime-gpu` НЕ має aarch64 wheels. Використовуйте індекс NVIDIA Jetson AI Lab.

### Ollama

Локальний LLM-інференс виконується нативно на хості через Ollama для GPU-прискорення
та щоб не роздувати контейнер. SelenaCore спілкується з Ollama по HTTP API
(`OLLAMA_URL`, за замовчуванням `http://localhost:11434`).

```bash
# Встановити Ollama (одноразово)
curl -fsSL https://ollama.com/install.sh | sh

# Увімкнути та запустити systemd-сервіс
sudo systemctl enable --now ollama

# Завантажити модель
ollama pull qwen2.5:3b

# Перевірити
curl http://localhost:11434/api/tags
```

Хмарні LLM-провайдери (OpenAI, Anthropic, Google AI, Groq) налаштовуються через UI
голосових налаштувань і не потребують жодного сервісу на хості.

---

## Змінні середовища

Усі налаштування керуються через файл `.env` у кореневій директорії проєкту. Скопіюйте `.env.example` до `.env` та налаштуйте за потребою.

| Змінна | За замовчуванням | Опис |
|--------|-----------------|------|
| `CORE_PORT` | `80` | Порт API-сервера |
| `CORE_DATA_DIR` | `/var/lib/selena` | Директорія даних (БД, моделі) |
| `CORE_SECURE_DIR` | `/secure` | Директорія зашифрованих секретів |
| `CORE_LOG_LEVEL` | `INFO` | Рівень логування |
| `DEBUG` | `false` | Увімкнути режим налагодження та Swagger UI |
| `PLATFORM_API_URL` | `https://selenehome.tech/api/v1` | URL хмарної платформи |
| `PLATFORM_DEVICE_HASH` | *(порожнє)* | Хеш ідентифікації пристрою |
| `UI_PORT` | `80` | Порт веб-інтерфейсу |
| `UI_HTTPS` | `true` | Увімкнути HTTPS для інтерфейсу |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Шлях до Docker-сокету |
| `MODULE_CONTAINER_IMAGE` | `smarthome-modules:latest` | Образ контейнера для користувацьких модулів |
| `GOOGLE_CLIENT_ID` | *(порожнє)* | Ідентифікатор клієнта Google OAuth |
| `GOOGLE_CLIENT_SECRET` | *(порожнє)* | Секрет Google OAuth |
| `TUYA_CLIENT_ID` | *(порожнє)* | Ідентифікатор клієнта інтеграції Tuya |
| `TUYA_CLIENT_SECRET` | *(порожнє)* | Секрет інтеграції Tuya |
| `TAILSCALE_AUTH_KEY` | *(порожнє)* | Ключ автентифікації Tailscale VPN |
| `GEMINI_API_KEY` | *(порожнє)* | Ключ для хмарного LLM (резервний) |
| `DEV_MODULE_TOKEN` | *(порожнє)* | Токен розробки для тестування |
| `OLLAMA_MODELS_DIR` | *(порожнє)* | Директорія зберігання моделей Ollama |

---

## Конфігурація core.yaml

Основний файл конфігурації знаходиться за адресою `/opt/selena-core/config/core.yaml`. Дивіться [Довідник конфігурації](../configuration.md) для всіх доступних опцій.

---

## Сервіси systemd

Щоб запускати SelenaCore як системний сервіс при завантаженні, встановіть наступний unit-файл.

### smarthome-core.service

```ini
# /etc/systemd/system/smarthome-core.service
[Unit]
Description=SelenaCore Smart Home Hub
After=docker.service
Requires=docker.service

[Service]
WorkingDirectory=/opt/selena-core
ExecStart=/usr/bin/docker compose up
ExecStop=/usr/bin/docker compose down
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Увімкнення та запуск:

```bash
sudo systemctl daemon-reload
sudo systemctl enable smarthome-core.service
sudo systemctl start smarthome-core.service
```

### Додаткові сервіси

| Сервіс | Призначення |
|--------|-------------|
| `smarthome-agent.service` | Агент моніторингу цілісності |
| `piper-tts.service` | Нативний Piper TTS HTTP-сервер (встановлюється тільки коли `voice.tts.primary.cuda: true`) |
| `selena-display.service` | Wayland kiosk-дисплей (встановлюється автоматично коли є `cage` + підключений DRM-вихід) |

---

## Майстер початкового налаштування

При першому запуску SelenaCore переходить у режим налаштування та проводить користувача через початкову конфігурацію.

1. Створює точку доступу WiFi: `SmartHome-Setup`
2. Відкриває веб-майстер за адресою `http://192.168.4.1`
3. Кроки майстра:
   - Вибір мови
   - Налаштування мережі WiFi
   - Назва пристрою
   - Вибір голосового рушія
   - Створення профілю користувача
   - Налаштування дисплея
   - Підключення до платформи
4. Після завершення система перезавантажується у звичайному режимі

---

## Експлуатація

### Перевірка стану

Перевірте, що система працює коректно:

```bash
curl http://localhost/api/v1/health
```

Очікувана відповідь:

```json
{
  "status": "ok",
  "version": "...",
  "mode": "normal",
  "uptime": 12345,
  "integrity": "ok"
}
```

### Перегляд логів

```bash
# Follow core logs in real time
docker compose logs -f selena-core

# Filter logs for a specific module
docker compose logs -f selena-core | grep "module-name"

# View log files on disk
ls /var/log/selena/
```

### Директорії даних

| Шлях | Вміст |
|------|-------|
| `/var/lib/selena/` | База даних SQLite, голосові моделі, резервні копії |
| `/var/lib/selena/models/vosk/` | Моделі Vosk STT |
| `/var/lib/selena/models/piper/` | Моделі Piper TTS |
| `/secure/` | Зашифровані токени, AES-ключі |
| `/secure/module_tokens/` | Токени автентифікації модулів |

---

## Оновлення

Завантажте останні зміни та перезберіть:

```bash
cd /opt/selena-core
git pull
docker compose build
docker compose up -d
```

Альтернативно, використовуйте системний модуль `update_manager` для автоматичних оновлень по повітрю.

---

## Резервне копіювання

Системний модуль `backup_manager` виконує автоматичне резервне копіювання:

- **Локальні резервні копії:** база даних SQLite та файли конфігурації
- **Хмарні резервні копії:** на налаштоване віддалене сховище

Для ручного резервного копіювання скопіюйте директорії даних та секретів:

```bash
sudo cp -r /var/lib/selena/ /path/to/backup/selena_data/
sudo cp -r /secure/ /path/to/backup/selena_secure/
```

---

## Усунення несправностей

| Проблема | Рішення |
|----------|---------|
| **Порт 80 зайнятий** | Змініть `CORE_PORT` у `.env` та перезапустіть |
| **Немає аудіо виходу або входу** | Перевірте монтування `/dev/snd` в `docker-compose.yml`; перевірте пристрої через `aplay -l` та `arecord -l` в контейнері; використовуйте `plughw:X,Y` для ALSA |
| **Модуль не підключається** | Переконайтеся, що `MODULE_TOKEN` та `SELENA_BUS_URL` правильно задані в середовищі модуля |
| **Система увійшла в безпечний режим** | Перевірте логи агента цілісності (`docker compose logs selena-agent`); переконайтеся, що хеші файлів ядра відповідають очікуваним значенням |
| **Docker: відмова в доступі** | Переконайтеся, що поточний користувач у групі `docker`, або запускайте з `sudo` |
| **Моделі Ollama не завантажуються** | Переконайтеся, що `OLLAMA_MODELS_DIR` вказує на існуючу директорію з достатнім обсягом дискового простору |
