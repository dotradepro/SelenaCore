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
     |-- Tier 1:   FastMatcher (IntentCompiler, regex з БД)  ~0 мс
     |             - composite device patterns (один regex на дієслово)
     |             - verb-bucket pre-filter за першим словом
     |             - сортування priority + specificity
     |             - тільки англійські патерни за дизайном
     |-- Tier 2:   Module Bus (модулі користувача, WebSocket) ~мс
     |-- Cache:    IntentCache (SQLite, попередні LLM hits)   ~10 мс
     |             - гарячі фрази (>=5 hits) авто-промотуються у Tier 1
     |-- Tier 3:   Local LLM (Ollama, один виклик)            300-800 мс
     |             - динамічний промпт з registry-aware device-by-room контекстом
     |-- Tier 4:   Cloud LLM (OpenAI-сумісний, опціонально)   1-3 сек
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

Усі патерни зберігаються в базі даних. Повний deep-dive — у [intent-routing.md](intent-routing.md).

| Таблиця | Призначення |
|---------|-------------|
| `intent_definitions` | Ім'я інтенту, модуль, пріоритет, опис |
| `intent_patterns` | Regex патерни (тільки `lang="en"` читається) |
| `intent_vocab` | Дієслова, іменники, параметри (legacy) |

**Жорсткі інтенти модулів** не сидяться через скрипт — кожен модуль декларує `OWNED_INTENTS` + `_OWNED_INTENT_META` і вставляє/claims рядки на `start()` через `_claim_intent_ownership()`.

**Composite-патерни пристроїв**: `PatternGenerator.rebuild_composite_device_patterns()` створює максимум 5 рядків на весь реєстр (по одному на дієслово: `device.on`, `device.off`, `device.set_temperature`, `device.lock`, `device.unlock`) з `(?P<name>...)` alternation усіх імен пристроїв. Захоплене ім'я резолвиться у `device_id` за O(1) через in-memory індекс.

**Hot-cache промоція**: фрази, які hit'нули кеш `>=5` разів, раз на годину промотуються у `auto_learned` рядки. Тільки англійською — UK/RU/DE запити продовжують йти через LLM (~500мс) → IntentCache (~10мс).

**Hot-reload** при device CRUD: PatternGenerator → IntentCompiler.full_reload() → IntentRouter.refresh_system_prompt(). Без перезапуску.

`scripts/seed_intents_to_db.py` — **legacy**: сидить лише weather/privacy правила, для системних модулів не потрібен.

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
