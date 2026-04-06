# Довідник конфігурації

SelenaCore використовує систему конфігурації з двох джерел: змінні середовища (через `.env` та оточення shell) та YAML-файл (`core.yaml`). Цей документ описує кожне доступне налаштування, його тип, значення за замовчуванням та призначення.

---

## Джерела конфігурації та пріоритет

Налаштування вирішуються у такому порядку, від найвищого до найнижчого пріоритету:

1. **Змінні середовища** — задані у shell або середовищі виконання контейнера
2. **Файл `.env`** — завантажується автоматично через Pydantic `BaseSettings`
3. **`core.yaml`** — налаштування, які можна змінювати під час роботи, завантажені з диску
4. **Значення за замовчуванням** — жорстко закодовані у класі `CoreSettings`

Це означає, що змінна середовища завжди перевизначає те саме налаштування, визначене у `.env` або `core.yaml`.

### Розташування файлів

| Файл | Шлях за замовчуванням | Перевизначення |
|------|----------------------|----------------|
| `.env` | Корінь проєкту (`.env`) | Н/Д |
| `core.yaml` | `/opt/selena-core/config/core.yaml` | Задайте змінну середовища `SELENA_CONFIG` для альтернативного шляху |
| `logging.yaml` | `/opt/selena-core/config/logging.yaml` | Н/Д |

---

## Довідник CoreSettings (.env / Змінні середовища)

Усі налаштування нижче визначені у `core/config.py` як модель Pydantic `BaseSettings`. Їх можна задати як змінні середовища або розмістити у файлі `.env`.

### Platform

| Змінна | Тип | За замовчуванням | Опис |
|--------|-----|-----------------|------|
| `PLATFORM_API_URL` | `str` | `https://smarthome-lk.com/api/v1` | URL хмарної платформи API SelenaCore. |
| `PLATFORM_DEVICE_HASH` | `str` | `""` | Унікальний ідентифікатор пристрою, зареєстрований на платформі. |
| `MOCK_PLATFORM` | `bool` | `False` | Коли `True`, усі виклики API платформи повертають заглушки. Корисно для офлайн-розробки. |

### Core

| Змінна | Тип | За замовчуванням | Опис |
|--------|-----|-----------------|------|
| `CORE_PORT` | `int` | `80` | TCP-порт, на якому слухає API-сервер core. |
| `CORE_DATA_DIR` | `str` | `/var/lib/selena` | Каталог для постійних даних (база даних, стан модулів). |
| `CORE_SECURE_DIR` | `str` | `/secure` | Каталог для секретів та конфіденційних файлів (токени, ключі). |
| `CORE_LOG_LEVEL` | `str` | `INFO` | Рівень деталізації логування. Один із: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `DEBUG` | `bool` | `False` | Увімкнення режиму налагодження у всьому додатку (детальний вивід, автоперезавантаження). |

### UI

| Змінна | Тип | За замовчуванням | Опис |
|--------|-----|-----------------|------|
| `UI_HTTPS` | `bool` | `True` | Чи запускати TLS-проксі для HTTPS на порту 443. |

> **Примітка:** UI обслуговується тим самим процесом, що й Core API на порту 80. Окремого `UI_PORT` немає — він був видалений при об'єднанні UI-проксі з Core.

### Agent

| Змінна | Тип | За замовчуванням | Опис |
|--------|-----|-----------------|------|
| `AGENT_CHECK_INTERVAL` | `int` | `30` | Інтервал у секундах між перевірками стану модулів. |
| `AGENT_MAX_RESTORE_ATTEMPTS` | `int` | `3` | Максимальна кількість автоматичних спроб перезапуску модуля, що зазнав збою. |

### Docker

| Змінна | Тип | За замовчуванням | Опис |
|--------|-----|-----------------|------|
| `DOCKER_SOCKET` | `str` | `/var/run/docker.sock` | Шлях до сокета демона Docker. |
| `MODULE_CONTAINER_IMAGE` | `str` | `smarthome-modules:latest` | Docker-образ за замовчуванням для запуску контейнерів модулів. |
| `SANDBOX_IMAGE` | `str` | `smarthome-sandbox:latest` | Docker-образ для ізольованого виконання коду. |

### OAuth

| Змінна | Тип | За замовчуванням | Опис |
|--------|-----|-----------------|------|
| `GOOGLE_CLIENT_ID` | `str` | `""` | OAuth 2.0 client ID для інтеграції з Google (Assistant, Calendar). |
| `GOOGLE_CLIENT_SECRET` | `str` | `""` | OAuth 2.0 client secret для інтеграції з Google. |
| `TUYA_CLIENT_ID` | `str` | `""` | Client ID платформи Tuya IoT. |
| `TUYA_CLIENT_SECRET` | `str` | `""` | Client secret платформи Tuya IoT. |

### Tailscale

| Змінна | Тип | За замовчуванням | Опис |
|--------|-----|-----------------|------|
| `TAILSCALE_AUTH_KEY` | `str` | `""` | Ключ попередньої аутентифікації для автоматичного підключення до Tailscale VPN. |

### Обчислювані властивості

Ці властивості обчислюються під час виконання і не можуть бути задані безпосередньо:

| Властивість | Значення | Опис |
|-------------|----------|------|
| `db_url` | `sqlite+aiosqlite:////{core_data_dir}/selena.db` | Рядок асинхронного підключення SQLAlchemy до бази даних SQLite. |
| `secure_dir_path` | `Path(core_secure_dir)` | Об'єкт `pathlib.Path` для каталогу секретів. |

---

## Довідник core.yaml

YAML-файл конфігурації призначений для налаштувань, які можуть змінюватися під час роботи через UI або майстер налаштування. Скопіюйте `config/core.yaml.example` до `/opt/selena-core/config/core.yaml` як відправну точку.

### core

```yaml
core:
  host: "0.0.0.0"
  port: 80
  data_dir: "/var/lib/selena"
  secure_dir: "/secure"
  log_level: "INFO"
  debug: false
```

| Ключ | Тип | За замовчуванням | Опис |
|------|-----|-----------------|------|
| `host` | `str` | `0.0.0.0` | Адреса прив'язки API-сервера core. |
| `port` | `int` | `80` | TCP-порт API-сервера core. |
| `data_dir` | `str` | `/var/lib/selena` | Каталог постійних даних. |
| `secure_dir` | `str` | `/secure` | Каталог безпечного зберігання секретів. |
| `log_level` | `str` | `INFO` | Рівень логування (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). |
| `debug` | `bool` | `false` | Увімкнення режиму налагодження. |

### ui

```yaml
ui:
  host: "0.0.0.0"
  port: 80
  https: true
```

| Ключ | Тип | За замовчуванням | Опис |
|------|-----|-----------------|------|
| `host` | `str` | `0.0.0.0` | Адреса прив'язки сервера веб-інтерфейсу. |
| `port` | `int` | `80` | TCP-порт веб-інтерфейсу. |
| `https` | `bool` | `true` | Обслуговувати UI через HTTPS. |

### agent

```yaml
agent:
  check_interval_sec: 30
  max_restore_attempts: 3
```

| Ключ | Тип | За замовчуванням | Опис |
|------|-----|-----------------|------|
| `check_interval_sec` | `int` | `30` | Інтервал у секундах між перевірками стану модулів. |
| `max_restore_attempts` | `int` | `3` | Максимальна кількість автоматичних спроб перезапуску модуля, що зазнав збою. |

### modules

```yaml
modules:
  container_image: "smarthome-modules:latest"
  sandbox_image: "smarthome-sandbox:latest"
```

| Ключ | Тип | За замовчуванням | Опис |
|------|-----|-----------------|------|
| `container_image` | `str` | `smarthome-modules:latest` | Docker-образ для контейнерів модулів. |
| `sandbox_image` | `str` | `smarthome-sandbox:latest` | Docker-образ для ізольованого виконання. |

### voice

```yaml
voice:
  wake_word_sensitivity: 0.5
  stt_model: "vosk-model-small-uk"
  stt_silence_timeout: 1.0
  rephrase_enabled: false
  output_volume: 50               # Загальна гучність TTS (0-150%)
  input_gain: 100                 # Підсилення мікрофона (0-150%)
  audio_force_input: null         # ALSA пристрій захоплення (авто якщо null)
  audio_force_output: null        # ALSA пристрій відтворення (авто якщо null)
  privacy_gpio_pin: null          # GPIO-пін для фізичного вимикача мікрофона
  tts:
    primary:
      voice: "uk_UA-ukrainian_tts-medium"
      lang: "uk"
      cuda: true
      settings:
        length_scale: 0.65
        noise_scale: 0.667
        noise_w_scale: 0.8
        volume: 0.7
        speaker: 1
    fallback:
      voice: "en_US-ryan-low"
      lang: "en"
      cuda: false
      settings:
        length_scale: 0.75
        noise_scale: 0.667
        noise_w_scale: 0.8
        volume: 0.55
        speaker: 0
```

| Ключ | Тип | За замовчуванням | Опис |
|------|-----|-----------------|------|
| `wake_word_sensitivity` | `float` | `0.5` | Поріг чутливості для слова активації (0.0-1.0). |
| `stt_model` | `str` | `vosk-model-small-uk` | Назва моделі Vosk STT (завантажується з alphacephei.com/vosk/models). |
| `stt_silence_timeout` | `float` | `1.0` | Секунди тиші перед обробкою команди (0.5-5.0). |
| `rephrase_enabled` | `bool` | `false` | LLM перефразування відповідей модулів. Додає затримку. |
| `output_volume` | `int` | `100` | Загальна гучність TTS 0-150%. Програмне масштабування PCM. |
| `input_gain` | `int` | `100` | Підсилення мікрофона 0-150%. Застосовується через `amixer`. |
| `audio_force_input` | `str\|null` | `null` | ALSA пристрій захоплення (напр., `plughw:0,0`). |
| `audio_force_output` | `str\|null` | `null` | ALSA пристрій відтворення (напр., `plughw:1,3`). |
| `privacy_gpio_pin` | `int\|null` | `null` | GPIO-пін для фізичного вимикача мікрофона. |
| `tts.primary.voice` | `str` | `uk_UA-ukrainian_tts-medium` | Основний голос Piper TTS. |
| `tts.primary.lang` | `str` | `uk` | Код мови основного голосу. |
| `tts.primary.cuda` | `bool` | `false` | GPU-прискорення для основного голосу. |
| `tts.primary.settings.*` | `dict` | див. вище | Параметри синтезу для кожного голосу. |
| `tts.fallback.voice` | `str` | `en_US-ryan-low` | Резервний (англійський) голос. |
| `tts.fallback.lang` | `str` | `en` | Мова резервного голосу. |
| `tts.fallback.settings.*` | `dict` | див. вище | Параметри синтезу для кожного голосу. |

**Налаштування TTS для кожного голосу:**

| Налаштування | Діапазон | За замовчуванням | Опис |
|--------------|----------|-----------------|------|
| `length_scale` | 0.3-2.0 | 1.0 | Швидкість мовлення (менше = швидше). |
| `noise_scale` | 0.0-1.0 | 0.667 | Варіативність інтонації. |
| `noise_w_scale` | 0.0-1.0 | 0.8 | Варіативність ширини фонем. |
| `volume` | 0.1-3.0 | 1.0 | Гучність синтезу (нативна Piper). |
| `speaker` | 0-N | 0 | ID мовця для багатомовцевих моделей. |

### Змінні оточення (голос/TTS/LLM)

| Змінна | За замовчуванням | Опис |
|--------|-----------------|------|
| `PIPER_MODELS_DIR` | `/var/lib/selena/models/piper` | Каталог голосових моделей Piper |
| `PIPER_VOICE` | `uk_UA-ukrainian_tts-medium` | Голос TTS за замовчуванням |
| `PIPER_GPU_URL` | `http://localhost:5100` | URL нативного сервера Piper |
| `PIPER_DEVICE` | `auto` | Режим пристрою Piper: `auto`, `cpu`, `gpu` |
| `LLAMACPP_GPU_LAYERS` | `999` | Кількість GPU-шарів для llama.cpp (0=тільки CPU) |
| `LLAMACPP_N_CTX` | `512` | Розмір контекстного вікна для llama.cpp |

### llm

```yaml
llm:
  enabled: true
  provider: "ollama"
  ollama_url: "http://localhost:11434"
  default_model: "phi-3-mini"
  min_ram_gb: 5
  timeout_sec: 30
```

| Ключ | Тип | За замовчуванням | Опис |
|------|-----|-----------------|------|
| `enabled` | `bool` | `true` | Увімкнення локальної підсистеми LLM. |
| `provider` | `str` | `ollama` | Провайдер інференсу LLM. Наразі підтримується: `ollama`. |
| `ollama_url` | `str` | `http://localhost:11434` | Базова URL-адреса API Ollama. |
| `default_model` | `str` | `phi-3-mini` | Модель за замовчуванням для класифікації інтентів та розмовних відповідей. |
| `min_ram_gb` | `int` | `5` | Мінімальний обсяг доступної RAM (у ГБ), необхідний перед завантаженням моделі. |
| `timeout_sec` | `int` | `30` | Тайм-аут запиту у секундах для викликів інференсу LLM. |

### platform

```yaml
platform:
  api_url: "https://smarthome-lk.com/api/v1"
  device_hash: ""
  heartbeat_interval_sec: 60
  mock: false
```

| Ключ | Тип | За замовчуванням | Опис |
|------|-----|-----------------|------|
| `api_url` | `str` | `https://smarthome-lk.com/api/v1` | Endpoint API хмарної платформи. |
| `device_hash` | `str` | `""` | Ідентифікатор пристрою для реєстрації на платформі. |
| `heartbeat_interval_sec` | `int` | `60` | Інтервал у секундах між heartbeat-пінгами до платформи. |
| `mock` | `bool` | `false` | Заглушити всі відповіді API платформи для офлайн-розробки. |

### wizard

```yaml
wizard:
  completed: false
  current_step: null
```

| Ключ | Тип | За замовчуванням | Опис |
|------|-----|-----------------|------|
| `completed` | `bool` | `false` | Чи завершено початковий майстер налаштування. |
| `current_step` | `str` або `null` | `null` | Останній активний крок майстра, використовується для відновлення перерваного налаштування. |

### system

```yaml
system:
  device_name: "Selena Hub"
  language: "uk"
  timezone: "Europe/Kyiv"
```

| Ключ | Тип | За замовчуванням | Опис |
|------|-----|-----------------|------|
| `device_name` | `str` | `Selena Hub` | Зручна для людини назва цього екземпляра хаба. |
| `language` | `str` | `uk` | Код мови системи (ISO 639-1). |
| `timezone` | `str` | `Europe/Kyiv` | Ідентифікатор часового поясу IANA для планування та відображення. |

---

## Додаткові змінні .env

Ці змінні не є частиною `CoreSettings`, але використовуються допоміжними сервісами та інструментами розробки.

| Змінна | Опис |
|--------|------|
| `GEMINI_API_KEY` | API-ключ для Google Gemini, використовується як хмарний резервний LLM, коли локальний інференс недоступний. |
| `APP_URL` | Базова URL-адреса API core (наприклад, `http://localhost`). Використовується зовнішніми сервісами, яким потрібно робити зворотні виклики до SelenaCore. |
| `HOST_UID` | UID користувача хоста, передається у контейнери для дозволів сокета PulseAudio. |
| `OLLAMA_MODELS_DIR` | Альтернативний каталог, де Ollama зберігає завантажені моделі. |
| `DEV_MODULE_TOKEN` | Статичний bearer-токен, що приймається під час розробки для тестування API модулів. Не використовуйте у продакшені. |

---

## Конфігурація логування

Логування налаштовується через `/opt/selena-core/config/logging.yaml`, який завантажується за допомогою `logging.config.dictConfig()` Python.

Якщо файл відсутній або не вдалося завантажити, SelenaCore використовує `basicConfig` Python із рівнем, взятим зі змінної середовища `CORE_LOG_LEVEL` (за замовчуванням `INFO`).

---

## Оновлення конфігурації під час роботи

Налаштування, що зберігаються у `core.yaml`, можуть бути змінені під час роботи через:

- **Майстер налаштування** — записує початкову конфігурацію під час першого запуску.
- **Панель налаштувань UI** — дозволяє змінювати налаштування голосу, LLM та системи без перезапуску.

Ці оновлення обробляються модулем `core/config_writer.py`, який читає поточний YAML, застосовує зміни та атомарно записує файл назад.

---

## Приклад швидкого старту

1. Скопіюйте приклади файлів:

   ```bash
   cp config/core.yaml.example /opt/selena-core/config/core.yaml
   cp .env.example .env
   ```

2. Відредагуйте `.env`, додавши облікові дані та секрети:

   ```dotenv
   PLATFORM_DEVICE_HASH=your-device-hash
   GOOGLE_CLIENT_ID=your-google-client-id
   GOOGLE_CLIENT_SECRET=your-google-client-secret
   TAILSCALE_AUTH_KEY=tskey-auth-xxxxx
   ```

3. Налаштуйте `core.yaml` для вашого середовища (мова, часовий пояс, голосова модель).

4. Запустіть SelenaCore. Майстер налаштування проведе вас через решту конфігурації, якщо `wizard.completed` має значення `false`.
