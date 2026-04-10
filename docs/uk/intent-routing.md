# Маршрутизація інтентів — Поглиблений огляд архітектури

> Доповнення до [voice-settings.md](voice-settings.md), [architecture.md](architecture.md)
> та [system-module-development.md](system-module-development.md). Цей файл — єдине джерело
> істини для того, ЯК SelenaCore перетворює голосову команду на дію модуля.
> Інші доки посилаються сюди замість дублювання.
>
> English version: [docs/intent-routing.md](../intent-routing.md)

## 1. Пайплайн коротко

```
  audio (arecord)
       │
       ▼
  Vosk STT  ────►  text + stt_lang
       │
       ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  IntentRouter.route(text, lang)                              │
  │                                                              │
  │  Tier 1   FastMatcher (regex з БД, тільки English)  ~0 мс   │
  │  Tier 2   Module Bus (модулі користувача, WS)       ~мс     │
  │  Cache    IntentCache (SQLite, попередні LLM hits)  ~10 мс  │
  │  Tier 3   Local LLM (Ollama, один виклик)           300-800 │
  │  Tier 4   Cloud LLM (OpenAI-сумісний, опціонально)  1-3 сек │
  │  Fallback "не зрозумів"                                     │
  └─────────────────────────────────────────────────────────────┘
       │
       ▼  EventBus: voice.intent { intent, params, source }
  Модуль-власник інтенту виконує
       │
       ▼
  Dual Piper TTS  →  динамік
```

**Ключові інваріанти**

- **Уся pipeline працює англійською** внутрішньо. Починаючи з v0.4
  переклад виконується [Argos Translate](translation.md) на краях
  пайплайну (після Vosk STT, перед Piper TTS), а не LLM.
- IntentRouter отримує **вже англійський текст** і випускає англійський
  `intent` + англійський `params.location` / `params.entity` +
  англійський `response`. Українських / російських / німецьких
  FastMatcher-патернів немає і не буде (`IntentCompiler.match()` за
  дизайном ходить тільки по `patterns["en"]`).
- Мова *відповіді* TTS обробляється `OutputTranslator` (en→target_lang)
  безпосередньо перед `preprocess_for_tts` і Piper.
- Усі рівні маршрутизації проходять через `IntentRouter` і випускають
  один `voice.intent` event з уніфікованим payload.

**Переклад можна вимкнути.** Коли `translation.enabled=false` або
користувач використовує тільки англійську (Vosk EN + Piper EN), обидва
перекладачі замикаються накоротко (~0 мс passthrough). Тоді система
очікує текст англійською напряму від Vosk.

## 2. Звідки беруться інтенти

Існує рівно два типи інтентів:

| Тип | Власник | Lifecycle | Приклад |
|-----|---------|-----------|---------|
| **Жорсткі (hard)** | Модуль декларує при старті | Перевстановлюються при кожному `module.start()` | `device.on`, `device.set_temperature`, `media.pause`, `clock.set_alarm` |
| **Динамічні** | `PatternGenerator` будує з рядків реєстру | Перебудовуються на CRUD entity | `media.play_radio_name` для «Hit FM», composite `device.on` для живого списку пристроїв |

**Центрального seed-файлу для жорстких інтентів немає.** Скрипт `scripts/seed_intents_to_db.py` сидить кілька legacy weather / privacy правил і поступово виводиться з ужитку — модулі є джерелом істини для того, що вони вміють.

### 2.1 Жорсткі інтенти — як модуль їх декларує

Кожен системний модуль експонує `_OWNED_INTENT_META` словник і метод `_claim_intent_ownership()`. На `start()` модуль:

1. Оновлює `intent_definitions.module = <self.name>` для кожного імені у `OWNED_INTENTS` (claim'ить існуючі рядки)
2. **Вставляє відсутні рядки** з метаданими з `_OWNED_INTENT_META` (description, noun_class, verb, priority)

Це робить модуль повністю самодостатнім — видалення і перевстановлення відновлює його каталог. Канонічна реалізація — у [system_modules/device_control/module.py](../../system_modules/device_control/module.py).

```python
# system_modules/device_control/module.py (фрагмент)
_OWNED_INTENT_META: dict[str, dict] = {
    INTENT_QUERY_TEMPERATURE: dict(
        noun_class="CLIMATE", verb="query", priority=100,
        description=(
            "Read the CURRENT temperature reported by an indoor climate "
            "device (air conditioner / thermostat) in a specific room. "
            "Returns the live sensor value, NOT the outdoor weather forecast."
        ),
    ),
    ...
}
```

Жорсткий інтент **не потребує жодного FastMatcher-патерну**. Достатньо щоб він був у `intent_definitions` — LLM-tier (Tier 3) побачить його у динамічному каталозі (`IntentCompiler.get_all_intents()` повертає рядки з нульовою кількістю скомпільованих патернів) і обере його для природньомовних висловлювань. Саме так працює сьогодні `device.query_temperature`.

### 2.2 Динамічні інтенти — composite-патерни пристроїв

Для реєстру пристроїв `PatternGenerator.rebuild_composite_device_patterns()` створює **максимум 5 рядків** на весь реєстр, незалежно від кількості пристроїв:

| Рядок | Дієслова | Пристрої |
|-------|----------|----------|
| `device.on` composite | turn on, switch on, enable | усі пристрої з `meta.name_en` |
| `device.off` composite | turn off, switch off, disable | усі пристрої з `meta.name_en` |
| `device.set_temperature` composite | set X to N | тільки клімат (`thermostat` / `air_conditioner`) |
| `device.lock` composite | lock, secure, shut | тільки замки (`lock` / `door_lock`) |
| `device.unlock` composite | unlock, open | тільки замки |

Кожен composite-патерн використовує **named-group alternation** з усіма відомими іменами пристроїв, відсортовану longest-first щоб багатослівні імена вигравали над їхніми префіксами:

```regex
^(?:turn\s+on|switch\s+on|enable)
 \s+(?:the\s+)?
 (?P<name>air\ conditioner|kitchen\ light|bedroom\ lamp|...)
 (?:\s+(?:in|on)\s+(?:the\s+)?(?P<location>living\ room|kitchen|...))?
 \s*\??$
```

Старі per-device рядки витираються при кожному rebuild, тому додавання чи видалення пристрою — це одна SQL-транзакція. Радіостанції та сцени все ще використовують per-entity патерни — їх текст більш різноманітний і вони не страждають від того ж row explosion.

### 2.3 Резолв збігнутого пристрою

Коли FastMatcher знаходить composite-рядок, захоплена `(?P<name>...)` група резолвиться у конкретний `device_id` за O(1) через in-memory індекс, побудований під час rebuild:

```python
gen = get_pattern_generator()
device_id = gen.get_device_id_by_name("air conditioner")  # → uuid або None
```

Два пристрої з однаковим `meta.name_en` (наприклад, дві `lamp`-и в різних кімнатах) **колізують**. Колізія детектується на rebuild:

- `_device_name_index` містить тільки **унікальні** імена → `get_device_id_by_name()` повертає `None`
- `_ambiguous_names` (set) тримає колізіонуючі імена
- `is_ambiguous_name(name)` повідомляє чи потрібна дисамбігуація

`device-control._on_voice_intent` перевіряє обидва. Для унікальних імен ін'єктить `params["device_id"]` і йде швидким шляхом `_resolve_device`. Для ambiguous імен ін'єктить `params["name_en"]`, і **tier-0** шлях `_resolve_device` шукає у реєстрі за `meta.name_en == name AND (location збігається з user-language АБО meta.location_en)`. Якщо все ще ambiguous, резолвер повертає `None` і користувач чує «Не знайшов кліматичний пристрій у спальні».

## 3. FastMatcher (Tier 1) — `IntentCompiler`

Джерело: [system_modules/llm_engine/intent_compiler.py](../../system_modules/llm_engine/intent_compiler.py)

`IntentCompiler` читає `intent_definitions` + `intent_patterns` зі SQLite, компілює кожен патерн у `re.Pattern`, експонує `match(text, lang)` для роутера.

### 3.1 Порядок патернів — `(priority DESC, specificity DESC)`

Раніше патерни з однаковим `priority` матчилися в невизначеному порядку. Тепер компілятор оцінює кожен патерн через `_pattern_specificity()`:

| Властивість | Бал |
|-------------|-----|
| Довжина | +1 за символ |
| Named group `(?P<...>...)` | +50 кожна |
| End anchor `$` / `\Z` | +30 |
| Start anchor `^` / `\A` | +30 |
| Word boundary `\b` | +20 |
| Non-capturing group `(?:...)` | +10 |
| Greedy wildcard `.*` / `.+` | -5 |

Усі англійські патерни сплющуються у `_flat_en` відсортований за `(-priority, -specificity)`. Це означає що параметризований патерн `set\s+...\s+(?P<level>\d+)$` завжди виграє над голим `set\s+...` навіть коли обидва мають priority 100.

### 3.2 Verb-bucket pre-filter

Типова голосова команда починається з одного з ~20 дієслів (`turn`, `set`, `play`, `what`, `how`, `lock`, …). `_VERB_BUCKETS` мапить кожне дієслово до його кандидатних інтентів:

```python
_VERB_BUCKETS = {
    "turn":   ("device.on", "device.off"),
    "switch": ("device.on", "device.off", "device.set_mode"),
    "set":    ("device.set_temperature", "device.set_fan_speed",
               "device.set_mode", "clock.set_alarm", "clock.set_timer"),
    "what":   ("weather.current", "weather.temperature",
               "device.query_temperature", "media.whats_playing"),
    ...
}
```

`_async_load()` будує `_buckets_en[verb] → list[(prio, spec, intent, entry)]` одноразово. `match()` читає перше слово вводу, пробує матчинг bucket'а спочатку (типовий розмір: 3-15 патернів) і падає назад на повний `_flat_en` walk (107+ патернів) тільки якщо bucket промахнувся.

Реальні виміри на реєстрі з 46 інтентів / 107 патернів:

| Перше слово | Розмір bucket'а | Старий scan |
|---|---|---|
| `turn` | 4 | 107 |
| `set` | 11 | 107 |
| `play` | 14 | 107 |
| `what` | 14 | 107 |

Слова, відсутні у `_VERB_BUCKETS`, проходять до повного scan — тобто пропуски коштують лише в продуктивності, ніколи в коректності.

### 3.3 Інтенти без патернів все одно у каталозі

Жорсткі інтенти на кшталт `device.query_temperature` можуть мати нуль скомпільованих патернів. `IntentCompiler` тримає їх у `_compiled` (і відповідно у `get_all_intents()`) щоб LLM-tier бачив їх у своєму динамічному каталозі. Вони просто відсутні у `_flat_en` і `_buckets_en` — FastMatcher їх ніколи не пробує, але LLM пробує.

## 4. IntentCache (між Tier 2 і Tier 3)

Джерело: [system_modules/llm_engine/intent_cache.py](../../system_modules/llm_engine/intent_cache.py)

Кожна успішна LLM-класифікація зберігається в SQLite-таблицю з ключем `(text, lang)` і `hit_count`. На наступне ідентичне висловлювання кеш повертає закешовані `intent` + `params` напряму, минаючи LLM round-trip повністю (~10 мс vs 300-800 мс).

### 4.1 Промоція гарячих фраз

Як тільки запис було hit'нуто `>= 5` разів, **promotion-loop** перетворює його на справжній FastMatcher-рядок:

- `IntentCache.promote_frequent_to_patterns()` запускається з `core/main.py` раз на годину
- Кожен promoted рядок використовує `source='auto_learned'` та `entity_ref='cache:promoted'`, відокремлений namespace від `auto_entity` щоб PatternGenerator-композитні rebuild'и їх не чіпали
- Патерн — anchored literal: `^<re.escape(text)>\??$`
- Після промоції викликається `IntentCompiler.full_reload()` і наступне висловлювання потрапляє на Tier 1 за ~0 мс

**Тільки англійською за дизайном.** Кеш все ще записує не-ASCII висловлювання для кеш-hit'ів, але крок промоції їх пропускає з лог-повідомленням — FastMatcher все одно б їх не читав. Українські / російські / німецькі запити продовжують платити LLM cost при першому зустрічі, потім IntentCache cost (~10 мс) при наступних.

## 5. LLM-tier (Tier 3) — динамічний registry context

Джерело: [system_modules/llm_engine/intent_router.py](../../system_modules/llm_engine/intent_router.py) — `_load_db_catalog()` і `_build_intent_catalog()`.

Локальний LLM (Ollama) промпт перебудовується при кожному device CRUD. Він містить:

1. **Зареєстровані інтенти** — кожен рядок у `intent_definitions` з ім'ям, описом, params schema. Включає інтенти без патернів.
2. **Підключені модулі** — display name і список інтентів кожного user / system модуля.
3. **Devices by room** — згруповані за `meta.location_en` з `entity_type: name_en` на пристрій:
   ```
   Devices by room (use the room name to scope intents):
     bedroom: light: bedside lamp, light: ceiling light
     kitchen: outlet: kettle, light: kitchen light
     living room: air_conditioner: air conditioner
   ```
4. **Known indoor rooms** — окремий список з правилом маршрутизації:
   > "If the user names any of these rooms, choose an intent that acts on or reads from a device in that room. Pick a non-room/global intent only when the user does NOT name any known room or explicitly says 'outside' / 'outdoor' / 'globally'."
5. **Радіостанції / сцени** — для media-player і automation-engine.
6. **TTS language directive** — `Response MUST be in <lang>`.

### 5.1 Обмеження розміру промпту

Дві модульні константи запобігають безмежному росту промпту:

```python
_DEVICES_PER_ROOM_LIMIT = 10
_ROOMS_LIMIT = 30
```

Будинок з 60 пристроями у 35 кімнатах продукує:
- До 30 видимих кімнат × 10 пристроїв на кожну = 300 entries
- `... (5 more rooms omitted)` футер для решти
- Кожен рядок кімнати скорочується з `(+N more)` якщо там більше 10 пристроїв

Це cap'ить каталог приблизно на 3-5 КБ тексту промпту — комфортно для 4K-context моделі типу `phi-3-mini`, з запасом для `gemma2:9b` яка тримає ~150 інтентів повністю.

### 5.2 Чому цього достатньо — без жорсткого мапу кімнат

У попередній ітерації існувала явна мапа `_OUTDOOR_TO_INDOOR_INTENT = {"weather.temperature": "device.query_temperature"}` плюс український morphology heuristic. Обидва видалено: з registry-aware промптом LLM сам обирає правильний інтент.

Перевірено end-to-end на тестовому стенді з Ollama:

| Ввід | Результат |
|-------|--------|
| «Яка температура у вітальні?» (uk) | `device.query_temperature` + `location=living room` |
| «Яка температура надворі?» (uk) | `weather.temperature` |
| «увімкни кондиціонер у вітальні» (uk) | `device.on` + `location=living room` + `entity=air_conditioner` |
| "what is the temperature in the bedroom" (en) | `device.query_temperature` + `location=bedroom` |
| "what is the temperature outside" (en) | `weather.temperature` |

Жодного хардкоду. LLM читає реєстр, бачить що `living room` має `air_conditioner`, і маршрутизує відповідно.

## 6. Lifecycle: голос → дія

```
1. Vosk:           audio → "Яка температура у вітальні?"
2. IntentRouter.route(text, lang="uk")
   ├─ Tier 1 FastMatcher       → miss (немає UK-патернів)
   ├─ Tier 2 Module Bus        → miss
   ├─ IntentCache              → miss (перший раз)
   ├─ Tier 3 Local LLM         → {"intent":"device.query_temperature",
   │                              "entity":"air_conditioner",
   │                              "location":"living room"}
   └─ IntentCache.put()        → закешовано на наступний раз
3. EventBus publish "voice.intent" з payload IntentResult
4. device-control._on_voice_intent
   ├─ intent = "device.query_temperature" → branch на _handle_query_temperature
   ├─ _resolve_device(entity_filter=("air_conditioner","thermostat"),
   │                  params={location:"living room"})
   ├─ device.state["current_temp"] = 22
   └─ speak_action("device.query_temperature",
                   {result:"ok", temperature:22, location:"living room"})
5. VoiceCore rephrase LLM      → «У вітальні зараз 22 градуси»
6. Piper TTS                   → audio
```

Та сама фраза на другому виклику йде Tier 1 → IntentCache hit (~10 мс) → device-control. Після 5 hit'ів і години аптайму у БД з'являється відповідний `auto_learned` рядок, і Tier 1 FastMatcher відповідає на третю зустріч за ~0 мс (тільки англійською).

## 7. Межі масштабування

| Метрика | Комфортно | Практичний ліміт |
|---------|-----------|-------------------|
| Жорстких інтентів у каталозі | 100 | 150 (бюджет LLM context) |
| Пристроїв у реєстрі | 150 | 300-500 з `gemma2:9b` |
| Кімнат у будинку | 30 | 50 з підвищеними лімітами |
| Пристроїв на кімнату | 10 | 15 |
| Унікальних `name_en` | 50 | 200 (час компіляції regex) |
| FastMatcher патернів загалом | 200 | 1000 |
| Latency на FastMatcher hit | ~0 мс | ~5 мс |
| Latency на cache hit | ~10 мс | ~30 мс |
| Latency на LLM hit | ~500 мс | ~1500 мс (cloud) |

Понад ~500 пристроїв архітектура потребує hierarchical routing (per-floor sharding) — це enterprise / building-automation scope, не single-house smart-home.

## 8. Як додати голосову команду до свого модуля

1. Оберіть унікальне ім'я інтенту в namespace вашого модуля: `mymodule.do_thing`.
2. Додайте його у `OWNED_INTENTS` і `_OWNED_INTENT_META` у класі модуля.
3. Підпишіться на `voice.intent` у `start()` і dispatch'те ім'я інтенту в обробнику.
4. Використовуйте `self.speak_action(intent, context)` щоб VoiceCore rephrase LLM створив природньомовну відповідь мовою TTS користувача.
5. **НЕ** додавайте патерни у seed-script. **НЕ** створюйте файли під `config/intents/` (цей шлях мертвий). Жорсткі інтенти живуть у модулі-власнику.

Якщо також хочете 0 мс FastMatcher shortcut для англійських команд — впишіть regex у `intent_patterns` з `source='manual'` і вашим intent_id, але це опційно. LLM-tier обробляє природню мову (будь-якою мовою) без жодних патернів.

Див. [system-module-development.md](system-module-development.md) для робочого прикладу зі шляхами файлів і повним кодом.

## 9. Посилання

- Джерело: [system_modules/llm_engine/intent_router.py](../../system_modules/llm_engine/intent_router.py)
- Джерело: [system_modules/llm_engine/intent_compiler.py](../../system_modules/llm_engine/intent_compiler.py)
- Джерело: [system_modules/llm_engine/intent_cache.py](../../system_modules/llm_engine/intent_cache.py)
- Джерело: [system_modules/llm_engine/pattern_generator.py](../../system_modules/llm_engine/pattern_generator.py)
- Джерело: [system_modules/device_control/module.py](../../system_modules/device_control/module.py) — канонічний hard-intent + composite resolver
- Пов'язані доки: [voice-settings.md](voice-settings.md), [architecture.md](architecture.md), [system-module-development.md](system-module-development.md), [climate-and-gree.md](climate-and-gree.md)
