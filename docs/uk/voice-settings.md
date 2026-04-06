# Налаштування голосового пайплайну

## Огляд пайплайну

```
Мікрофон (arecord, ALSA)
     |
     v
  Vosk STT (мова з конфігу, для кожної моделі) --> текст + stt_lang
     |
     v
  Intent Router
     |-- Tier 0:   IntentCompiler (regex патерни з БД)       ~0 мс
     |-- Tier 1:   Module Bus (модулі користувача, WebSocket) ~мс
     |-- Cache:    IntentCache (SQLite, попередні результати)  ~0 мс
     |-- Tier 2:   Local LLM (Ollama, один виклик)           300-800 мс
     |-- Tier 3:   Cloud LLM (OpenAI-сумісний, опціонально)  1-3 сек
     '-- Fallback: "не зрозумів" (i18n)
     |
     v
  Модуль виконує дію через EventBus
     |
     v
  Dual Piper TTS
     |-- Основний голос (мова системи, GPU)
     '-- Резервний голос (англійська, CPU)
     |
     v
  split_by_language() --> сегменти --> правильний голос для кожного
     |
     v
  aplay (ALSA прямий) --> Динамік
```

## Мовна архітектура

Два поняття мови -- не змішувати:

| Поняття | Джерело | Призначення |
|---------|---------|-------------|
| `stt_lang` | Мова моделі Vosk (з конфігу) | Regex matching, ключ кешу |
| `tts_lang` | Конфіг Piper `voice.tts.primary.lang` | Мова відповіді, вибір голосу |

Правила:
- `stt_lang == primary_lang` --> основний голос, відповідь мовою системи
- `stt_lang != primary_lang` --> резервний EN голос, відповідь англійською
- EventBus payload: intent/entity/location/params завжди **англійською**
- Текст відповіді: мовою `tts_lang`

## STT -- Vosk

Розпізнавання мовлення через Vosk (нативно, без контейнера). Vosk використовує потокове розпізнавання (чанк за чанком) замість пакетної транскрипції, видаючи результати по мірі отримання аудіо.

| Платформа | Модель | Затримка |
|-----------|--------|----------|
| Jetson Orin | vosk-model-small-uk | ~150мс |
| Linux x86_64 | vosk-model-small-uk | ~100мс |
| Raspberry Pi 5 | vosk-model-small-uk | ~300мс |
| Raspberry Pi 4 | vosk-model-small-uk | ~500мс |

Моделі завантажуються з [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models) та зберігаються локально. Для кожної мови потрібна окрема модель.

Конфігурація:

```yaml
stt:
  provider: vosk
  vosk:
    models_dir: /var/lib/selena/models/vosk
    active_model: vosk-model-small-uk
```

Vosk також підтримує **режим граматики** для виявлення слова активації -- обмежений словник, що покращує точність та зменшує навантаження CPU під час постійного прослуховування.

Мова визначається активною моделлю (окремі моделі для кожної мови, не авто-визначення з мовлення).

## TTS -- Dual Piper (piper1-gpl)

Дві моделі PiperVoice завантажуються при старті, обидві гарячі в пам'яті:

| Голос | Призначення | Модель | GPU | RAM |
|-------|-------------|--------|-----|-----|
| Основний | Мова системи | uk_UA-ukrainian_tts-medium | Так | ~65 МБ |
| Резервний | Англійська | en_US-ryan-low | Ні | ~5 МБ |

### Конфігурація

```yaml
voice:
  output_volume: 50
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

## Система інтентів -- патерни з БД

Всі патерни зберігаються в базі даних:

| Таблиця | Призначення |
|---------|-------------|
| `intent_definitions` | Ім'я інтенту, модуль, пріоритет |
| `intent_patterns` | Regex патерни по мовах |
| `intent_vocab` | Дієслова, іменники, параметри |

Авто-генерація при додаванні радіостанцій, пристроїв, сцен. Hot-reload без перезапуску.

## Конфігурація LLM

```yaml
ai:
  conversation:
    provider: "local"
    local:
      host: "http://localhost:11434"
      model: "qwen2.5:3b"
      options:
        temperature: 0.1
        num_predict: 80
    cloud:
      url: "https://api.groq.com/openai/v1"
      key: "${GROQ_API_KEY}"
      model: "llama-3.1-8b-instant"
```

## RAM бюджет (Jetson 8GB headless)

```
OS headless              0.65 ГБ
Vosk small model         0.05 ГБ
SelenaCore + модулі      0.30 ГБ
qwen2.5:3b (Ollama Q4)  2.00 ГБ
Piper uk medium (GPU)    0.065 ГБ
Piper en low (CPU)       0.005 ГБ
-----------------------------------------
Разом:                   3.07 ГБ
Вільно:                  4.93 ГБ
```
