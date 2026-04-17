# Маршрутизація інтентів — поглиблений огляд

> Доповнення до `CLAUDE.md §20` та [voice-settings.md](voice-settings.md).
> Єдине джерело істини для того, як SelenaCore класифікує та диспетчеризує
> голосові / текстові команди користувача.
>
> English version: [docs/intent-routing.md](../intent-routing.md)
>
> Застарілі FastMatcher (regex), pattern-рядки у `IntentCompiler`, IntentCache,
> composite device patterns та LLM-як-класифікатор **повністю прибрані**.
> Кожен запит класифікується заново на живому стані БД.

## 1. Пайплайн коротко

```
                                        ┌────────────────────┐
 Звук ─► Vosk / Whisper ─► текст ─► ── │ InputTranslator    │ ─► англ. текст
                                        │ (Argos / Helsinki) │
                                        └────────────────────┘

 ┌────────────────────────────────────────────────────────────┐
 │                        IntentRouter                        │
 │                                                            │
 │  Tier 0   Module Bus (WebSocket → user modules)    ~50 мс  │
 │  Tier 1   Embedding classifier (MiniLM-L6-v2)      ~50 мс  │
 │           cosine над per-utterance каталогом               │
 │  Tier 2   Assistant LLM (chat prompt, БЕЗ каталогу) 300-800 │
 │           розмовна відповідь, intent="unknown"             │
 │  Fallback детермінована фраза "Я не зрозуміла"             │
 └────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    publish("voice.intent", payload)
                              │
                ┌─────────────┴─────────────┐
                ▼                           ▼
        System-модуль                 VoiceCore озвучує
        виконує дію                   assistant / fallback відповідь
        + self.speak_action()
```

**Ключові файли:**

- `system_modules/llm_engine/intent_router.py` — оркестрація 0/1/2 рівнів
- `system_modules/llm_engine/embedding_classifier.py` — MiniLM-L6-v2 ONNX cosine
- `system_modules/llm_engine/intent_compiler.py` — живий кеш рядків `intent_definitions`
- `core/module_loader/system_module.py::_claim_intent_ownership` — реєстрація статичних інтентів
- `core/api/helpers.py::on_entity_changed` — invalidation hook на CRUD пристроїв / станцій / сцен

**Інтенти класифікує embedding-модель, а НЕ LLM.** LLM — розмовний fallback для висловлювань, які класифікатор помітив як `unknown`. Він не бачить каталог інтентів і повертає лише natural-language відповідь, ніколи не intent-мітку.

## 2. Звідки беруться інтенти

### 2.1 Статичні — `OWNED_INTENTS` + `_OWNED_INTENT_META`

Кожен системний модуль оголошує свої інтенти на класі:

```python
class WeatherServiceModule(SystemModule):
    name = "weather-service"

    OWNED_INTENTS = [
        "weather.current",
        "weather.forecast",
        "weather.temperature",
    ]

    _OWNED_INTENT_META: dict[str, dict] = {
        "weather.current": dict(
            noun_class="WEATHER", verb="query", priority=100,
            description=(
                "Report the CURRENT outdoor weather conditions "
                "(temperature + summary). Use for 'what's the weather' "
                "style questions. NOT for indoor AC / thermostat readings."
            ),
        ),
        # ... по одному запису на кожен інтент
    }

    async def start(self) -> None:
        self.subscribe(["voice.intent"], self._on_event)
        await self._claim_intent_ownership()   # idempotent
```

`SystemModule._claim_intent_ownership()` (у `core/module_loader/system_module.py`):

1. `UPDATE intent_definitions SET module=self.name WHERE intent IN OWNED_INTENTS` — «привласнює» вже існуючі рядки.
2. `UPDATE description, entity_types` з `_OWNED_INTENT_META` — модуль є єдиним джерелом істини для формулювання, яке бачить класифікатор.
3. `INSERT` відсутні рядки з `_OWNED_INTENT_META`.

Виконується у кожному `start()` модуля — свіжий boot контейнера перереєстровує весь каталог за секунду. Змінили `description` у коді, перезапустили контейнер — наступний embedding classify бачить нову формулу.

### 2.2 Динамічні — пристрої / радіостанції / сцени

**Динамічних інтентів нема.** Сутності — це *слоти* на існуючих статичних інтентах, не нові intent-мітки.

- `device.on` + `params.name="спальня лампа"` — НЕ новий `device.turn_on_bedroom_light`
- `media.play_radio_name` + `params.station_name="Радіо Релакс"` — НЕ новий `media.play_radio_relax`

Коли пристрій / станцію додано через `POST /api/v1/devices` або `POST /api/ui/modules/media-player/radio`, роут викликає `core.api.helpers.on_entity_changed(entity_type, id, action)`, який:

1. `IntentCompiler.full_reload()` — перебудовує in-memory intent catalog. Наступний embedding classify бачить повний свіжий набір інтентів.
2. Для `entity_type == "device"`: `PatternGenerator.rebuild()` оновлює індекс `name_en → device_id`, який device-control використовує для перетворення `params.name` від класифікатора на реальний пристрій.
3. Публікує `REGISTRY_ENTITY_CHANGED` на EventBus для інших модулів.

## 3. Tier 1 — Embedding-класифікатор

### 3.1 Per-utterance фільтр каталогу

`IntentRouter._build_filtered_catalog(user_text, native_text)` збирає кандидатів на один запит:

```
tokens = tokenize(user_text) ∪ tokenize(native_text)    # Unicode \w{3,}

Intents:
  для кожного інтенту з IntentCompiler.get_all_intents():
    якщо tokens ∩ (tokenize(description) ∪ tokenize(intent_name)) != ∅:
      включити інтент з description обрізаним до 120 символів
  завжди додати "unknown" як bail-out

Devices:
  для кожного пристрою у registry:
    якщо tokens ∩ tokenize(name_en, name, location_en, location) != ∅:
      включити рядок пристрою (білінгвально)

Radio stations:
  для кожної станції:
    якщо tokens збігаються з name_user / name_en / genre_*:
      включити рядок станції

→ повертає (catalog_text, allowed_intent_set)
```

Фільтр білінгвальний: токени ЯК з англійського пост-Argos тексту, ТАК і з оригінального native тексту, йдуть в match-set. Команда «вимкни лампу у спальні» все одно включить "bedroom light" у filtered catalog — «спальня» потрапляє в українське поле `meta.location` пристрою.

### 3.2 Cosine similarity + пороги впевненості

`_parse_catalog_to_candidates(catalog_text)` витягає блок `Intents:` у список `{"name", "description"}`. `EmbeddingIntentClassifier.classify(query, candidates)` робить один forward pass MiniLM-L6-v2 над `[query, desc1, desc2, ...]` і повертає `(intent, score, runner_up, margin, params)`.

Два пороги з конфігу (ключі під `intent.*`):

| Ключ | Default | Сенс |
|---|---|---|
| `embedding_score_threshold` | `0.30` | Абсолютний cosine floor (query vs winner) |
| `embedding_margin_threshold` | `0.05` | Переможець − runner-up |

Нижче будь-якого → `_embedding_classify` повертає `None` → роутер падає у Tier 2.

**Allowed-set guard** відкидає будь-який інтент, якого нема у filtered `allowed` set — захист від MiniLM, що повертає якусь фразу, якої не було у списку кандидатів.

### 3.3 Post-processing для імперативів

`device.set_mode` / `device.set_temperature` інколи вигравали cosine у `device.on` / `device.off` на прикордонних фразах ("turn on the air conditioning"). Коротка евристика переключає відповідь класифікатора назад на `device.on` / `device.off` коли користувач сказав голу on/off-команду БЕЗ параметра mode / value. Дивися `_ON_VERBS` / `_OFF_VERBS` у `intent_router.py::_embedding_classify`.

### 3.4 Чому MiniLM а не LLM

| | MiniLM-L6-v2 (ONNX) | Local LLM (phi-3-mini / qwen 1.5b) |
|---|---|---|
| Затримка | ~50 мс | 300-2000 мс |
| Пам'ять | ~30 MB | ~1-5 GB |
| Детермінованість | так — обирає зі списку кандидатів | ні — галюцинує intent-імена |
| Не-англійські мови | через translator + білінгвальний фільтр | кошмар prompt engineering |
| Запускається на | будь-який Pi / x86 / Jetson | GPU-only для розумної затримки |

Класифікатор не намагається *розуміти* — він міряє семантичну схожість між висловлюванням і текстом опису. Цього достатньо щоб обрати правильний інтент і уникнути всіх пасток prompt-engineering маленьких моделей.

### 3.5 Що писати в `description`

Текст опису — єдине, що бачить MiniLM. Два правила:

1. **Почніть з дієслова + іменника, які скаже користувач.** `"Turn a device on (light, switch, AC, curtain, vacuum)..."` б'є `"Powers a device on."` — фраза "turn on" ближча до "Turn" у embedding-просторі.
2. **Додавайте негативи для близьких пар інтентів.** `device.query_temperature` і `weather.temperature` cosine-близькі до "what's the temperature". Фраза *"Returns the live sensor value, NOT the outdoor weather forecast"* їх розділяє.

Описи обрізаються до 120 символів у filtered prompt block — стисло краще за багатослівно.

## 4. Tier 0 — Module Bus

Користувацькі модулі (type=UI / INTEGRATION / DRIVER) реєструють свої інтенти через WebSocket Module Bus. `IntentRouter.route()` питає bus ПЕРЕД запуском embedding-класифікатора — якщо якийсь user module каже `handled=true`, він перемагає, і класифікатор не запускається. Це дає user-модулям змогу перекривати built-in поведінку (наприклад, кастомний weather-модуль може забрати `weather.current` у `weather-service`).

Див. `core/module_bus/` і доку SDK модулів для деталей протоколу.

## 5. Tier 2 — Assistant LLM

`IntentRouter._ask_as_assistant(text)` — ОСТАННІЙ рівень. Викликається лише коли:

- Tier 0 Module Bus промазав, AND
- Tier 1 Embedding повернув `unknown` або low confidence, AND
- `intent.llm_assistant_enabled` = `true` (default), AND
- Провайдер налаштований (`voice.llm_provider` встановлений), AND
- Вільна RAM ≥ `llm.min_ram_gb` (default 5)

```python
reply = await llm_call(
    text,
    prompt_key="chat",        # з PromptStore
    temperature=0.7,
    max_tokens=100,
    num_ctx=2048,
)
→ IntentResult(intent="unknown", response=reply, source="assistant")
```

**LLM ніколи не бачить intent catalog.** chat-prompt — це системний prompt на кшталт "You are a helpful home-assistant. Keep answers short..." — користувач отримує людську відповідь замість роботизованого "Я не зрозуміла", але жодного нового інтенту не створюється.

Якщо LLM повернув порожньо або рівень вимкнено, роутер повертає `IntentResult(intent="unknown", response="<детермінована фраза>", source="fallback")`.

## 6. Доставка через EventBus

`IntentRouter.route()` публікує `voice.intent` з результатом класифікації. Кожен system-модуль, що володіє інтентами у цьому namespace, підписаний на цю подію і виконує свою дію:

```python
async def _on_voice_intent(self, event):
    payload = event.payload or {}
    if payload.get("intent") not in self.OWNED_INTENTS:
        return
    # ... виконуємо дію, потім:
    await self.speak_action(payload["intent"], {"result": "ok", ...})
```

`speak_action()` делегує TTS-формулювання rephrase-LLM у VoiceCore, щоб відповідь потрапила до користувача рідною мовою, попри те, що класифікатор бігав над англійською.

## 7. Резолюція сутностей

Для інтентів, що діють на конкретну сутність (пристрій, станція, сцена), `params.name` від класифікатора виходить прямо з висловлювання ("bedroom light"). `device-control::_resolve_device()` використовує `PatternGenerator.get_device_id_by_name()` для мепу ім'я → `device_id` за O(1). Неоднозначні імена (два пристрої з однаковим `name_en`) fallback'аться на диспет за `params.location`.

Радіостанції / сцени йдуть через `IntentRouter._resolve_entity_ref()`, який шукає `RadioStation` / `Scene` за `name_user` чи `name_en` і інжектить `params.entity_ref` для хендлера.

## 8. Що прибрано

Стара архітектура мала 5 рівнів. Усе крім Module Bus і LLM-як-чату **прибрано**:

| Прибрано | Замінено на |
|---|---|
| FastMatcher regex (`IntentCompiler.match()`, `_flat_en`, verb buckets, pattern specificity) | Embedding classifier |
| `intent_patterns` regex-рядки, composite device patterns | Embedding classifier читає `intent_definitions.description` напряму |
| `PatternGenerator.rebuild_composite_device_patterns()` | `PatternGenerator.rebuild()` — звичайний name → device_id індекс |
| IntentCache + `auto_learned` hot-phrase promotion | Свіжий classify на кожен запит (жодного stale вказівника на віддалену сутність) |
| LLM-як-класифікатор з dynamic registry-aware prompt | LLM — лише chat-fallback, каталогу в prompt немає |
| `config/intents/`, `definitions.yaml`, `vocab/*.yaml` | `OWNED_INTENTS` + `_OWNED_INTENT_META` на класі кожного модуля |
| `scripts/seed_intents_to_db.py` | `_claim_intent_ownership()` у базовому `SystemModule` |
| `intent_cache.db`, hourly promotion loop у lifespan | — |

Що вижило:

- Таблиця `intent_definitions`: статичний каталог, пишеться `_claim_intent_ownership()`, читається `IntentCompiler.get_all_intents()`.
- `IntentCompiler`: зведено до живого кеша рядків `intent_definitions`.
- `PatternGenerator`: зведено до name → device_id lookup index для резолюції сутностей.
- `on_entity_changed`: незмінна точка тригера на CRUD — тепер лише оновлює кеш IntentCompiler та індекс PatternGenerator.
