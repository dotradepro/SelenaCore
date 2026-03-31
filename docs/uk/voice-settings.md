# Конфігурація голосового конвеєра

## Огляд конвеєра

Wake word → Запис аудіо → Vosk STT → Ідентифікація мовця (resemblyzer) → Intent Router (6 рівнів) → Cloud LLM Rephrase → Piper TTS

```
Мікрофон (parecord)
     │
     ▼
  Vosk STT ──► текст
     │
     ▼
  Intent Router
     ├── Tier 1:   Fast Matcher (ключові слова/regex)     ~0 мс
     ├── Tier 1.5: Інтенти системних модулів (в процесі)  ~мкс
     ├── Tier 2:   Module Bus (модулі, WebSocket)          ~мс
     ├── Tier 3a:  Cloud LLM класифікація (Gemini/…)       ~1-2 сек
     ├── Tier 3b:  Ollama LLM (локальний, RAM ≥ 5 ГБ)      3-8 сек
     └── Fallback: i18n "не зрозумів"
     │
     ▼
  Модуль виконує дію
     │
     ▼
  Cloud LLM Rephrase (варіативний TTS)
     │
     ▼
  Piper TTS (нативний сервер хоста, CPU/GPU) → Динамік
```

## STT - Vosk

- Офлайн-розпізнавання мовлення (рушій Kaldi)
- Оптимізовано для ARM на Raspberry Pi
- Моделі: tiny, base, small, medium (у `/var/lib/selena/models/vosk/`)
- Налаштовується в core.yaml: `voice.stt_model`

## TTS - Piper

- Синтез мовлення на основі ONNX через нативний сервер хоста (`piper-server.py`)
- Моделі завантажуються один раз та зберігаються у пам'яті (~100-400 мс CPU, ~30-80 мс GPU)
- Режим CPU/GPU: `--device auto|cpu|gpu` (автовизначення CUDAExecutionProvider)
- Моделі в `/var/lib/selena/models/piper/`
- Налаштовується в core.yaml: `voice.tts_voice`, `voice.tts_settings`

### Сервер Piper TTS

Працює нативно на хості (не в Docker) як systemd-сервіс на порту 5100.

```bash
# Запуск вручну
python3 scripts/piper-server.py --port 5100 --device auto

# systemd-сервіс
sudo systemctl enable --now piper-tts
```

**Ендпоінти:**

| Метод | Шлях | Опис |
|-------|------|------|
| POST | `/synthesize` | Текст → WAV-аудіо |
| POST | `/synthesize/raw` | Текст → сирий PCM s16le (для потокового відтворення через paplay) |
| GET | `/health` | Статус, пристрій (cpu/gpu), завантажені голоси |
| GET | `/voices` | Список встановлених голосових моделей |

**GPU-прискорення:** Потребує `onnxruntime-gpu` з CUDAExecutionProvider.

На Jetson (JetPack 6, CUDA 12.x):

```bash
# Автоматичне встановлення (рекомендовано)
bash scripts/build-onnxruntime-gpu.sh

# Або вручну:
pip3 install --user onnxruntime-gpu --extra-index-url https://pypi.jetson-ai-lab.io/jp6/cu126
pip3 install --user "numpy<2"                    # NumPy 2.x несумісний
sudo ln -sf /usr/lib/aarch64-linux-gnu/libcudnn.so.9 /usr/lib/aarch64-linux-gnu/libcudnn.so
sudo systemctl restart piper-tts
```

> **Примітка:** PyPI `onnxruntime-gpu` НЕ підтримує aarch64. Використовуйте індекс NVIDIA Jetson AI Lab.

### Продуктивність TTS (Jetson Orin Nano)

| Текст | CPU (прогрітий) | GPU (оцінка) | Холодний старт |
|-------|----------------|-------------|----------------|
| Короткий (1 слово) | ~420 мс | ~280 мс | ~2500 мс |
| Середній (4 слова) | ~780 мс | ~500 мс | ~2500 мс |
| Довгий (15 слів) | ~2280 мс | ~740 мс | ~2500 мс |

## Wake Word

- openWakeWord / граматичний режим Vosk
- Чутливість: core.yaml `voice.wake_word_sensitivity` (від 0.0 до 1.0)

## Ідентифікація мовця

- Бібліотека resemblyzer для зняття голосового відбитка
- Лише локальна обробка, без хмари

## Режим приватності

- Перемикання голосовою командою або через GPIO-пін
- Події: `voice.privacy_on`, `voice.privacy_off`
- Конфігурація: `voice.privacy_gpio_pin`

---

## Маршрутизація інтентів (6 рівнів)

Маршрутизатор інтентів використовує каскадну систему. Кожен рівень перевіряється послідовно; перший збіг виграє.

| Рівень | Назва | Затримка | Механізм | Джерело |
|--------|-------|----------|----------|---------|
| 1 | Fast Matcher | ~0 мс | Правила на ключових словах/regex у YAML | `fast_matcher.py` |
| 1.5 | Інтенти системних модулів | ~мкс | Regex з іменованими групами, в процесі | `intent_router.py` |
| 2 | Module Bus | ~мс | WebSocket запит до модулів користувача | `module_bus.py` |
| 3a | Cloud LLM класифікація | ~1-2 сек | Структурований JSON через Gemini/OpenAI тощо | `cloud_providers.py` |
| 3b | Ollama LLM | 3-8 сек | Локальна семантична модель (RAM ≥ 5 ГБ) | `ollama_client.py` |
| — | Fallback | ~0 мс | i18n повідомлення "не зрозумів" | `i18n` |

### Tier 1: Fast Matcher

Правила на ключових словах та regex, визначені у `/opt/selena-core/config/intent_rules.yaml` або вбудовані за замовчуванням. Нульова затримка. Підтримує базове керування пристроями (світло, температура, приватність).

### Tier 1.5: Інтенти системних модулів

Системні модулі реєструють паттерни `SystemIntentEntry` при старті. Підтримують іменовані групи regex для вилучення параметрів (напр. `(?P<genre>rock|jazz)`). 28 інтентів зареєстровано у 6 модулях.

### Tier 2: Module Bus

Модулі користувача (у контейнерах) реєструють інтенти через WebSocket повідомлення `announce`. Module Bus підтримує відсортований індекс інтентів та маршрутизацію з circuit breaker.

### Tier 3a: Cloud LLM класифікація

Коли regex-рівні не спрацьовують, маршрутизатор відправляє команду до налаштованого хмарного LLM-провайдера для структурованої класифікації. Це критично на Raspberry Pi, де локальний Ollama вимкнений (RAM < 5 ГБ).

**Як це працює:**

1. Маршрутизатор динамічно будує каталог усіх зареєстрованих інтентів (Tier 1 + 1.5 + 2)
2. Відправляє промпт класифікації до хмарного LLM (temperature=0.0 для детермінованого результату)
3. LLM повертає структурований JSON: `{"intent": "media.play_radio", "params": {}}`
4. Для загальних питань LLM повертає: `{"intent": "llm.response", "params": {}, "response": "..."}`

**Підтримувані провайдери:** OpenAI, Anthropic, Google AI (Gemini), Groq

**Таймаут:** 15 секунд

### Tier 3b: Ollama LLM

Локальний запасний варіант для пристроїв з достатньою RAM (≥ 5 ГБ). Використовує компактний системний промпт для малих моделей. Автоматично вимикається на пристроях з малою RAM.

---

## Конфігурація Cloud LLM

```yaml
voice:
  llm_provider: "google"          # "ollama" | "llamacpp" | "openai" | "anthropic" | "google" | "groq"
  providers:
    google:
      api_key: "AIza..."
      model: "gemini-2.0-flash"
    openai:
      api_key: "sk-..."
      model: "gpt-4o-mini"
    anthropic:
      api_key: "sk-ant-..."
      model: "claude-3-haiku-20240307"
```

Налаштовується через UI: **Settings → System Modules → Voice Core → LLM Router**

---

## LLM Rephrase відповідей (опціонально)

Коли увімкнено (`voice.rephrase_enabled: true`), відповіді системних модулів перефразовуються через LLM перед відтворенням TTS. **Вимкнено за замовчуванням** для зменшення затримки (економить 3-10 сек на відповідь при локальному LLM).

**Як це працює (коли увімкнено):**

1. Модуль викликає `m.speak("Граю радіо станцію Kiss FM")`
2. Подія `voice.speak` надходить до voice-core
3. voice-core відправляє стандартний текст + контекст діалогу до LLM
4. LLM перефразовує текст природно (temperature=0.9 для варіативності)
5. Перефразований текст озвучується через Piper TTS
6. У разі недоступності LLM використовується оригінальний текст

**Сесія діалогу:** останні 20 повідомлень (користувач + асистент) зберігаються в пам'яті, скидаються після 5 хвилин бездіяльності.

---

## Консоль тестування команд

UI для дебагу голосових команд без необхідності говорити. Розташована:

**Settings → System Modules → Voice Core → Command Test Console** (внизу сторінки)

Можливості:
- Текстове поле для імітації голосових команд
- Чекбокс TTS (озвучити відповідь або лише показати результат)
- Повний трейс пайплайну з відображенням статусу кожного рівня (hit/miss/skip/error) з таймінгом
- Відображення результату: назва інтенту, рівень, затримка, текст відповіді, action, params
- Клавіша Enter для відправки

**API ендпоінт:** `POST /api/ui/modules/voice-core/test-command`

```json
// Запит
{"text": "увімкни радіо", "speak": false}

// Відповідь
{
  "ok": true,
  "input_text": "увімкни радіо",
  "lang": "uk",
  "intent": "media.play_radio",
  "source": "system_module",
  "latency_ms": 5,
  "duration_ms": 5,
  "trace": [
    {"tier": "1", "name": "Fast Matcher", "status": "miss", "ms": 1},
    {"tier": "1.5", "name": "System Module Intents", "status": "hit", "ms": 5, "detail": "media-player::media.play_radio", "registered": 28}
  ]
}
```

---

## Довідник голосових команд

### media-player (14 інтентів)

| Інтент | Опис | Приклад (UK) | Приклад (EN) |
|--------|------|--------------|--------------|
| `media.play_radio` | Увімкнути радіо | "увімкни радіо" | "play radio" |
| `media.play_genre` | Грати за жанром | "увімкни джаз" | "play jazz music" |
| `media.play_radio_name` | Грати станцію | "увімкни радіо Kiss FM" | "play station Kiss FM" |
| `media.play_search` | Пошук і відтворення | "знайди Yesterday" | "find Yesterday" |
| `media.pause` | Пауза | "пауза" | "pause" |
| `media.resume` | Продовжити | "продовжуй" | "resume" |
| `media.stop` | Стоп | "стоп" | "stop" |
| `media.next` | Наступний трек | "наступний" | "next" |
| `media.previous` | Попередній трек | "попередній" | "previous" |
| `media.volume_up` | Гучніше | "гучніше" | "louder" |
| `media.volume_down` | Тихіше | "тихіше" | "quieter" |
| `media.volume_set` | Встановити гучність | "гучність на 50" | "volume 50" |
| `media.whats_playing` | Що грає | "що грає" | "what's playing" |
| `media.shuffle_toggle` | Перемішати | "перемішай" | "shuffle" |

### weather-service (3 інтенти)

| Інтент | Опис | Приклад (UK) | Приклад (EN) |
|--------|------|--------------|--------------|
| `weather.current` | Поточна погода | "яка погода" | "what's the weather" |
| `weather.forecast` | Прогноз погоди | "прогноз на завтра" | "weather forecast" |
| `weather.temperature` | Температура | "скільки градусів" | "what's the temperature" |

### presence-detection (3 інтенти)

| Інтент | Опис | Приклад (UK) | Приклад (EN) |
|--------|------|--------------|--------------|
| `presence.who_home` | Хто вдома | "хто вдома" | "who is home" |
| `presence.check_user` | Перевірити користувача | "чи є Олена вдома" | "is Alice home" |
| `presence.status` | Статус присутності | "статус присутності" | "presence status" |

### automation-engine (4 інтенти)

| Інтент | Опис | Приклад (UK) | Приклад (EN) |
|--------|------|--------------|--------------|
| `automation.list` | Список автоматизацій | "які автоматизації" | "list automations" |
| `automation.enable` | Увімкнути автоматизацію | "увімкни автоматизацію X" | "enable automation X" |
| `automation.disable` | Вимкнути автоматизацію | "вимкни автоматизацію X" | "disable automation X" |
| `automation.status` | Статус автоматизацій | "статус автоматизацій" | "automation status" |

### energy-monitor (2 інтенти)

| Інтент | Опис | Приклад (UK) | Приклад (EN) |
|--------|------|--------------|--------------|
| `energy.current` | Поточне споживання | "яке споживання" | "power consumption" |
| `energy.today` | Енергія за сьогодні | "скільки електрики сьогодні" | "energy today" |

### device-watchdog (2 інтенти)

| Інтент | Опис | Приклад (UK) | Приклад (EN) |
|--------|------|--------------|--------------|
| `watchdog.status` | Статус пристроїв | "статус пристроїв" | "device status" |
| `watchdog.scan` | Сканування пристроїв | "перевір пристрої" | "scan devices" |

### Інтенти Fast Matcher (5 інтентів)

| Інтент | Опис | Приклад (UK) | Приклад (EN) |
|--------|------|--------------|--------------|
| `turn_on_light` | Увімкнути світло | "увімкни світло" | "turn on light" |
| `turn_off_light` | Вимкнути світло | "вимкни світло" | "turn off light" |
| `temperature_query` | Запит температури | "яка температура" | "what's the temperature" |
| `privacy_on` | Увімкнути приватність | "не слухай" | "privacy on" |
| `privacy_off` | Вимкнути приватність | "вийди з приватного" | "privacy off" |

---

## Голосові події

| Подія | Опис |
|-------|------|
| `voice.wake_word` | Виявлено wake word |
| `voice.recognized` | Текстовий результат STT |
| `voice.intent` | Знайдено інтент (включає intent, source, params, latency) |
| `voice.response` | Згенеровано текст TTS-відповіді |
| `voice.speak` | Запит на озвучення тексту (EventBus → voice-core) |
| `voice.speak_done` | TTS-відтворення завершено |
| `voice.privacy_on` | Режим приватності увімкнено |
| `voice.privacy_off` | Режим приватності вимкнено |

## Голосова конфігурація в core.yaml

```yaml
voice:
  wake_word_sensitivity: 0.5
  stt_model: "vosk-model-small-uk-v3-nano"
  stt_silence_timeout: 1.0            # секунди тиші перед обробкою (0.5-5.0)
  tts_voice: "uk_UA-ukrainian_tts-medium"
  rephrase_enabled: false              # LLM rephrase для відповідей модулів (додає затримку)
  tts_settings:
    length_scale: 1.0                  # швидкість мовлення (0.5=швидко, 2.0=повільно)
    noise_scale: 0.667                 # варіативність інтонації (0.0-1.0)
    noise_w_scale: 0.8                 # варіативність ширини фонем (0.0-1.0)
    sentence_silence: 0.2             # пауза між реченнями (секунди)
    volume: 1.0                        # гучність (0.1-3.0)
    speaker: 0                         # ID мовця для багатоголосних моделей
  privacy_gpio_pin: null
  llm_provider: "google"
  providers:
    google:
      api_key: "AIza..."
      model: "gemini-2.0-flash"
```

## WebRTC-стримінг

- Підтримка потокового передавання аудіо в реальному часі через WebRTC
- Використовується для голосової взаємодії через браузер
