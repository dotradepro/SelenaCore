# Довідник API для розробників модулів SelenaCore

Цей документ є повним довідником API для розробки системних та користувацьких модулів SelenaCore. Він охоплює базові класи, декоратори, EventBus, WebSocket Module Bus, систему інтентів, HTML-віджети та manifest.json.

---

## Зміст

1. [Огляд архітектури](#1-огляд-архітектури)
2. [SystemModule API Reference](#2-systemmodule-api-reference)
3. [SmartHomeModule API Reference](#3-smarthomemodule-api-reference)
4. [EventBus — Довідник подій](#4-eventbus--довідник-подій)
5. [Протокол WebSocket Module Bus](#5-протокол-websocket-module-bus)
6. [Гід по Widget/Settings HTML](#6-гід-по-widgetsettings-html)
7. [Система інтентів — Як додати голосові команди](#7-система-інтентів--як-додати-голосові-команди)
8. [manifest.json — Повний довідник](#8-manifestjson--повний-довідник)
9. [Повні приклади](#9-повні-приклади)

---

## 1. Огляд архітектури

SelenaCore підтримує два типи модулів з принципово різними моделями виконання:

### SYSTEM модулі (системні)

- Працюють **in-process** всередині процесу SelenaCore через `importlib`
- Наслідують базовий клас `SystemModule` (`core/module_loader/system_module.py`)
- Мають прямий доступ до EventBus через асинхронні зворотні виклики Python
- Мають прямий доступ до бази даних через спільну фабрику сесій SQLAlchemy
- Необов'язковий FastAPI-роутер монтується на `/api/ui/modules/{name}/`
- **~0 МБ** додаткових витрат RAM (без контейнерів, без серіалізації)
- Розташовані у каталозі `system_modules/`

### USER модулі (користувацькі)

- Працюють як **окремі процеси** у Docker-контейнерах
- Наслідують базовий клас `SmartHomeModule` (`sdk/base_module.py`)
- Спілкуються з ядром через **WebSocket Module Bus** (`ws://core/api/v1/bus`)
- Повна ізоляція: окремий процес, окрема файлова система
- Типи: `UI`, `INTEGRATION`, `DRIVER`, `AUTOMATION`, `IMPORT_SOURCE`

### Принцип ізоляції модулів

```
⛔ Модулі НЕ імпортують один з одного — жодних прямих залежностей
✅ Вся комунікація ТІЛЬКИ через EventBus (шину ядра)
✅ Якщо цільовий модуль не запущений — команди ігноруються коректно (graceful degradation)
✅ Порядок запуску модулів не має значення — інтенти реєструються при start()
```

```
┌──────────────────────────────────────────────────────────┐
│                    Процес SelenaCore                      │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │  voice-core  │  │ media-player │  │ llm-engine   │   │
│  │ (SystemModule)│  │(SystemModule)│  │(SystemModule)│   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                 │            │
│         └─────────┬───────┴─────────┬───────┘            │
│                   │                 │                     │
│              ┌────▼─────┐    ┌─────▼──────┐              │
│              │ EventBus │    │  SQLAlchemy │              │
│              │(in-proc) │    │  (DB доступ)│              │
│              └────┬─────┘    └────────────┘              │
│                   │                                      │
│            ┌──────▼──────┐                               │
│            │ Module Bus  │ ◄── WebSocket                 │
│            │ (WS сервер) │                               │
│            └──────┬──────┘                               │
└───────────────────┼──────────────────────────────────────┘
                    │
         ┌──────────┼──────────┐
         │          │          │
    ┌────▼───┐ ┌───▼────┐ ┌──▼──────┐
    │ weather│ │ tuya   │ │ email   │
    │ module │ │ bridge │ │ module  │
    │(Docker)│ │(Docker)│ │(Docker) │
    └────────┘ └────────┘ └─────────┘
       SmartHomeModule (WebSocket клієнт)
```

---

## 2. SystemModule API Reference

Базовий клас: `core.module_loader.system_module.SystemModule`

Системні модулі наслідують цей ABC-клас та реалізують `start()` і `stop()`.

### Контракт підкласу

1. Задати атрибут класу `name`, що збігається з `"name"` у `manifest.json`
2. Реалізувати `start()` та `stop()`
3. За потреби реалізувати `get_router()` → `APIRouter`
4. В `__init__.py` експортувати: `module_class = YourModule`

### Методи

| Метод | Сигнатура | Опис |
|-------|-----------|------|
| `setup` | `setup(bus, session_factory) -> None` | Впровадження залежностей ядра. Викликається завантажувачем перед `start()`. Не перевизначати. |
| `start` | `async start() -> None` | **Абстрактний.** Ініціалізація сервісу, підписка на події, реєстрація інтентів. |
| `stop` | `async stop() -> None` | **Абстрактний.** Скасування фонових задач, звільнення ресурсів, зняття підписок. |
| `get_router` | `get_router() -> APIRouter \| None` | Повертає FastAPI-роутер, що монтується на `/api/ui/modules/{name}/`. За замовчуванням `None`. |

### EventBus хелпери

| Метод | Сигнатура | Опис |
|-------|-----------|------|
| `subscribe` | `subscribe(event_types: list[str], callback: Callable) -> str` | Підписка на події EventBus через прямий асинхронний зворотній виклик. Повертає `sub_id`. Колбек: `async def handler(event) -> None`. |
| `publish` | `async publish(event_type: str, payload: dict) -> None` | Публікація події в EventBus від імені модуля (`source=self.name`). |

### TTS хелпер

| Метод | Сигнатура | Опис |
|-------|-----------|------|
| `speak` | `async speak(text: str, *, timeout: float = 30.0) -> None` | Публікує `voice.speak` та **чекає** завершення TTS (`voice.speak_done`). Гарантує, що мовлення завершиться до продовження виконання. |

### Device Registry хелпери

| Метод | Сигнатура | Опис |
|-------|-----------|------|
| `fetch_devices` | `async fetch_devices() -> list[dict]` | Повертає всі зареєстровані пристрої як список словників. |
| `get_device_state` | `async get_device_state(device_id: str) -> dict` | Повертає словник стану конкретного пристрою. Повертає `{}` якщо пристрій не знайдений. |
| `patch_device_state` | `async patch_device_state(device_id: str, state: dict) -> None` | Оновлює стан пристрою в реєстрі та комітить транзакцію. |
| `register_device` | `async register_device(name, type, protocol, capabilities, meta) -> str` | Реєструє новий пристрій. Повертає `device_id`. |

### Роутер хелпери

| Метод | Сигнатура | Опис |
|-------|-----------|------|
| `_register_html_routes` | `_register_html_routes(router, module_file) -> None` | Реєструє ендпоінти `/widget` та `/settings` для HTML-файлів. Викликати в кінці `get_router()`. |
| `_register_health_endpoint` | `_register_health_endpoint(router) -> None` | Реєструє мінімальний `GET /health` ендпоінт: `{"status": "ok", "module": name}`. |

### Внутрішні методи

| Метод | Сигнатура | Опис |
|-------|-----------|------|
| `_cleanup_subscriptions` | `_cleanup_subscriptions() -> None` | Знімає всі підписки EventBus. Викликати в `stop()`. |
| `_db_session` | `async _db_session() -> AsyncGenerator[AsyncSession]` | Контекстний менеджер для створення сесії SQLAlchemy. |

### Приклад мінімального системного модуля

```python
# system_modules/my_sensor/__init__.py
from .module import MySensorModule as module_class  # noqa: F401

# system_modules/my_sensor/module.py
import logging
from fastapi import APIRouter
from core.module_loader.system_module import SystemModule

logger = logging.getLogger(__name__)


class MySensorModule(SystemModule):
    name = "my-sensor"

    async def start(self) -> None:
        self.subscribe(["device.state_changed"], self._on_state_changed)
        logger.info("MySensorModule started")

    async def stop(self) -> None:
        self._cleanup_subscriptions()
        logger.info("MySensorModule stopped")

    def get_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/data")
        async def get_data() -> dict:
            devices = await self.fetch_devices()
            return {"devices": devices}

        self._register_html_routes(router, __file__)
        self._register_health_endpoint(router)
        return router

    async def _on_state_changed(self, event) -> None:
        payload = event.payload
        logger.info("Device %s changed state", payload.get("device_id"))
```

---

## 3. SmartHomeModule API Reference

Базовий клас: `sdk.base_module.SmartHomeModule`

Користувацькі модулі наслідують цей клас та використовують декоратори для оголошення інтентів, обробників подій та планових задач. Комунікація відбувається через WebSocket Module Bus.

### Атрибути класу

| Атрибут | Тип | За замовчуванням | Опис |
|---------|-----|------------------|------|
| `name` | `str` | `"unnamed_module"` | Назва модуля, повинна збігатися з `manifest.json`. |
| `version` | `str` | `"0.1.0"` | Версія модуля (semver). |

### Декоратори

#### `@intent(pattern, order=50, name="", description="")`

Реєструє асинхронний обробник інтенту за regex-шаблоном.

| Параметр | Тип | Опис |
|----------|-----|------|
| `pattern` | `str` | Regex-шаблон для зіставлення з текстом користувача (case-insensitive). |
| `order` | `int` | Пріоритет у індексі шини (менше = вищий пріоритет). 0-29 системні, 30-49 ядро, 50-99 користувацькі. |
| `name` | `str` | Назва інтенту для каталогу LLM (наприклад, `"email.check_inbox"`). |
| `description` | `str` | Людино-зрозумілий опис для контексту LLM. |

```python
@intent(r"weather|forecast|погода|прогноз", order=50,
        name="weather.current", description="Current weather query")
async def handle_weather(self, text: str, context: dict) -> dict:
    return {"tts_text": "Зараз 22 градуси", "data": {"temp": 22}}
```

**Контракт відповіді:**

```python
{
    "handled": True,     # обов'язково — чи оброблено запит
    "tts_text": "...",   # текст для озвучення (TTS)
    "data": { ... }      # довільні дані (необов'язково)
}
```

#### `@on_event(event_type)`

Підписка на події EventBus. Підтримує шаблони з `*` (наприклад, `device.*`).

```python
@on_event("device.state_changed")
async def on_device_change(self, data: dict) -> None:
    device_id = data.get("device_id")
    self._log.info("Пристрій %s змінив стан", device_id)
```

#### `@scheduled(cron)`

Планувальник задач. Підтримує прості інтервали та стандартний cron.

| Формат | Приклад | Опис |
|--------|---------|------|
| Простий інтервал | `"every:30s"`, `"every:5m"`, `"every:1h"` | Виконання кожні N секунд/хвилин/годин |
| Стандартний cron | `"*/5 * * * *"` | Cron-вираз (потребує `apscheduler`) |

```python
@scheduled("every:5m")
async def check_status(self) -> None:
    self._log.info("Перевірка статусу кожні 5 хвилин")
```

### Методи життєвого циклу

| Метод | Сигнатура | Опис |
|-------|-----------|------|
| `start` | `async start() -> None` | Точка входу. Викликає `on_start()`, запускає планові задачі та підключається до шини. Не перевизначати. |
| `on_start` | `async on_start() -> None` | Перевизначити: одноразова ініціалізація перед підключенням до шини. |
| `on_stop` | `async on_stop() -> None` | Перевизначити: очищення ресурсів при зупинці модуля. |
| `on_shutdown` | `async on_shutdown() -> None` | Перевизначити: швидкий хук при `shutdown` від ядра. Для збереження стану, не для очищення. |

### Методи комунікації

| Метод | Сигнатура | Опис |
|-------|-----------|------|
| `publish_event` | `async publish_event(event_type: str, payload: dict) -> bool` | Публікація події через шину. Буферизує у вихідній черзі, якщо з'єднання відсутнє. |
| `api_request` | `async api_request(method, path, body=None, timeout=10.0) -> dict` | Відправка API-запиту через шину та очікування відповіді. Викидає `TimeoutError` або `ConnectionError`. |
| `get_device` | `async get_device(device_id: str) -> dict \| None` | Отримання пристрою з реєстру SelenaCore через шину. |
| `handle_api_request` | `async handle_api_request(method, path, body) -> dict` | Перевизначити: обробка вхідних API-запитів від ядра (UI проксі → модуль). За замовчуванням повертає 404. |
| `update_capabilities` | `async update_capabilities() -> None` | Надсилає `re_announce` для оновлення можливостей без перепідключення. |

### Локалізація (i18n)

| Метод | Сигнатура | Опис |
|-------|-----------|------|
| `t` | `t(key: str, lang: str \| None = None, **kwargs) -> str` | Переклад ключа з автономних файлів локалі модуля. Fallback: запитана мова → `en` → сам ключ. |

Файли локалі розташовуються у каталозі `locales/` поруч з модулем:

```
my_module/
    locales/
        en.json    # {"greeting": "Hello, {name}!"}
        uk.json    # {"greeting": "Привіт, {name}!"}
```

```python
text = self.t("greeting", lang="uk", name="Олена")
# → "Привіт, Олена!"
```

### Приклад мінімального користувацького модуля

```python
# main.py
import asyncio
from sdk.base_module import SmartHomeModule, intent, on_event, scheduled


class MyModule(SmartHomeModule):
    name = "my-module"
    version = "1.0.0"

    async def on_start(self) -> None:
        self._log.info("Модуль ініціалізовано")

    async def on_stop(self) -> None:
        self._log.info("Модуль зупинено")

    @intent(r"my command|моя команда", name="mymodule.action")
    async def handle_command(self, text: str, context: dict) -> dict:
        return {"tts_text": self.t("response", lang=context.get("_lang"))}

    @on_event("device.state_changed")
    async def on_device_change(self, data: dict) -> None:
        self._log.info("Пристрій змінився: %s", data)

    @scheduled("every:1m")
    async def periodic_check(self) -> None:
        self._log.debug("Періодична перевірка")


if __name__ == "__main__":
    module = MyModule()
    asyncio.run(module.start())
```

---

## 4. EventBus — Довідник подій

EventBus є центральною шиною повідомлень SelenaCore. Всі модулі комунікують виключно через неї.

### Два механізми доставки

| Механізм | Для кого | Як працює |
|----------|----------|-----------|
| **DirectSubscription** | SYSTEM модулі (in-process) | EventBus викликає колбек безпосередньо через `asyncio.create_task()` |
| **WebSocket Bus** | USER модулі (Docker) | EventBus надсилає JSON-повідомлення через WebSocket |

### Повний перелік подій

#### core.* — Системні події (публікуються тільки ядром)

| Подія | Опис |
|-------|------|
| `core.startup` | Ядро запущено |
| `core.shutdown` | Ядро завершує роботу |
| `core.integrity_violation` | Агент виявив зміни у файлах ядра |
| `core.integrity_restored` | Агент успішно відкотив зміни |
| `core.safe_mode_entered` | Система перейшла в БЕЗПЕЧНИЙ РЕЖИМ |
| `core.safe_mode_exited` | БЕЗПЕЧНИЙ РЕЖИМ знято |

> **Обмеження:** модулі не можуть публікувати події `core.*` — API поверне `403 Forbidden`.

#### device.* — Події пристроїв

| Подія | Опис |
|-------|------|
| `device.state_changed` | Стан пристрою змінився в реєстрі |
| `device.registered` | Новий пристрій додано до реєстру |
| `device.removed` | Пристрій видалено з реєстру |
| `device.offline` | Немає heartbeat > 90 сек |
| `device.online` | Пристрій знову доступний |
| `device.discovered` | Сканер знайшов новий пристрій у мережі |

#### module.* — Події модулів

| Подія | Опис |
|-------|------|
| `module.installed` | Модуль встановлено та запущено |
| `module.started` | Модуль запущено |
| `module.stopped` | Модуль зупинено нормально |
| `module.error` | Модуль повернув помилку або впав |
| `module.removed` | Модуль видалено |

#### voice.* — Голосові події

| Подія | Опис |
|-------|------|
| `voice.wake_word` | Виявлено wake-word |
| `voice.recognized` | STT розпізнав запит |
| `voice.intent` | IntentRouter визначив інтент (див. [розділ 7](#7-система-інтентів--як-додати-голосові-команди)) |
| `voice.response` | Відповідь LLM/fallback готова (текст для TTS) |
| `voice.speak` | Запит на озвучення TTS (від будь-якого модуля) |
| `voice.speak_done` | Озвучення TTS завершено |
| `voice.privacy_on` | Режим приватності увімкнено |
| `voice.privacy_off` | Режим приватності вимкнено |

#### automation.* — Події автоматизації

| Подія | Опис |
|-------|------|
| `automation.rule_triggered` | Правило автоматизації спрацювало |
| `automation.scene_activated` | Сцену активовано |

#### sync.* — Синхронізація з платформою

| Подія | Опис |
|-------|------|
| `sync.command_received` | Отримано команду від платформи |
| `sync.command_ack` | Команду підтверджено |
| `sync.connection_lost` | З'єднання з платформою втрачено |
| `sync.connection_restored` | З'єднання відновлено |

#### registry.* — Події реєстру

| Подія | Опис |
|-------|------|
| `registry.scan_complete` | Мережеве сканування завершено |
| `registry.device_classified` | Пристрій автоматично класифіковано |

#### media.* — Медіа-події

| Подія | Опис |
|-------|------|
| `media.state_changed` | Стан відтворення змінився |

### Структура об'єкта події

```python
{
    "event_id": "uuid-...",
    "type": "device.state_changed",
    "source": "climate-module",
    "payload": {
        "device_id": "uuid-...",
        "old_state": {"temperature": 22.0},
        "new_state": {"temperature": 23.0}
    },
    "timestamp": 1710936000.0
}
```

---

## 5. Протокол WebSocket Module Bus

WebSocket Module Bus — комунікаційний рівень між ядром SelenaCore та зовнішніми (користувацькими) модулями.

### Точка підключення

```
ws://<host>/api/v1/bus?token=<module_token>
```

Вся комунікація між модулем та ядром проходить через цю єдину точку. Окремих портів для кожного модуля немає.

### Життєвий цикл з'єднання

```
Module                                          Core
  |                                               |
  |  WebSocket connect ?token=TOKEN               |
  |---------------------------------------------->|
  |                          перевірка токена      |
  |                          (reject -> close 4001)|
  |                                               |
  |              WebSocket accept()               |
  |<----------------------------------------------|
  |                                               |
  |  announce {...capabilities}                   |
  |---------------------------------------------->|
  |                                               |
  |              announce_ack {bus_id}            |
  |<----------------------------------------------|
  |                                               |
  |       двонаправлений цикл повідомлень         |
  |<--------------------------------------------->|
  |                                               |
  |              ping (кожні 15 сек)              |
  |<----------------------------------------------|
  |  pong                                         |
  |---------------------------------------------->|
  |                                               |
  |              shutdown {drain_ms}              |
  |<----------------------------------------------|
  |  (завершення роботи, закриття з'єднання)      |
  |---------------------------------------------->|
```

### Типи повідомлень

Кожне повідомлення — JSON-об'єкт з обов'язковим полем `type`.

#### announce (модуль → ядро)

Надсилається одразу після прийняття WebSocket-з'єднання. Оголошує ідентичність модуля та можливості.

```json
{
    "type": "announce",
    "module": "weather-module",
    "capabilities": {
        "intents": [
            {
                "patterns": {"en": ["weather", "forecast"], "uk": ["погода", "прогноз"]},
                "priority": 50,
                "name": "weather.current",
                "description": "Current weather query"
            }
        ],
        "subscriptions": ["device.state_changed"],
        "publishes": ["weather.updated"]
    }
}
```

#### announce_ack (ядро → модуль)

```json
{
    "type": "announce_ack",
    "status": "ok",
    "bus_id": "uuid-...",
    "warnings": []
}
```

Коди помилок при відхиленні:
- `invalid_token` — токен недійсний (фатально, не перепідключатися)
- `permission_denied` — немає прав (фатально)
- Код закриття `4001` — автентифікація не пройшла

#### intent (ядро → модуль)

Ядро надсилає розпізнаний текст для обробки модулем.

```json
{
    "type": "intent",
    "id": "req-uuid",
    "payload": {
        "text": "what's the weather",
        "lang": "en",
        "context": {"user_id": "user-1"}
    }
}
```

#### intent_response (модуль → ядро)

```json
{
    "type": "intent_response",
    "id": "req-uuid",
    "payload": {
        "handled": true,
        "tts_text": "Зараз 22 градуси",
        "data": {"temp": 22}
    }
}
```

#### event (двонаправлений)

```json
{
    "type": "event",
    "payload": {
        "event_type": "device.state_changed",
        "data": {"device_id": "...", "new_state": {"on": true}}
    }
}
```

#### api_request (двонаправлений)

Модуль може запитувати Core API або ядро може надсилати запит до модуля (UI проксі).

```json
{
    "type": "api_request",
    "id": "req-uuid",
    "method": "GET",
    "path": "/devices/device-123",
    "body": null
}
```

#### api_response (двонаправлений)

```json
{
    "type": "api_response",
    "id": "req-uuid",
    "status": 200,
    "body": {"device_id": "device-123", "name": "Термостат"}
}
```

#### ping / pong (ядро → модуль → ядро)

Ядро надсилає `ping` кожні 15 секунд. Модуль повинен відповісти `pong`. Три пропущені ping — відключення (код `4004`).

```json
{"type": "ping", "ts": 1710936000.0}
{"type": "pong", "ts": 1710936000.0}
```

#### shutdown (ядро → модуль)

```json
{
    "type": "shutdown",
    "drain_ms": 5000
}
```

Модуль має завершити поточну роботу протягом `drain_ms` мілісекунд та коректно вийти.

### Формат capabilities

```json
{
    "intents": [
        {
            "patterns": {"en": ["regex1", "regex2"], "uk": ["шаблон1"]},
            "priority": 50,
            "name": "module.intent_name",
            "description": "Human-readable description"
        }
    ],
    "subscriptions": ["device.*", "voice.intent"],
    "publishes": ["custom.event"]
}
```

---

## 6. Гід по Widget/Settings HTML

Системні модулі надають `widget.html` та `settings.html` як iframe всередині панелі керування SelenaCore. **Повну бібліотеку компонентів, спільні класи та рекомендації з layout див. у [widget-development.md](widget-development.md#спільна-бібліотека-компонентів).** Ця секція описує лише JS-хелпери та HTTP-контракт специфічний для API модулів.

### Обов'язкові спільні ресурси

Кожен віджет і сторінка налаштувань повинні підключати обидва:

```html
<link rel="stylesheet" href="/api/shared/theme.css">
<script src="/api/shared/widget-common.js"></script>
```

`widget-common.js` надає все нижче — не реімплементуйте `BASE`, `t()`, `applyLang`, слухач `message` чи обгортку над fetch у вашому модулі.

### BASE та fetch-хелпери

`widget-common.js` автоматично обчислює `BASE` з шляху iframe (відрізаючи `/widget.html` або `/settings.html`) і надає чотири fetch-обгортки, що автоматично додають auth-заголовки:

```js
apiGet('/status')                       // GET  → JSON
apiPost('/settings', { city: 'Kyiv' })  // POST → JSON
apiPatch('/config', { enabled: true })  // PATCH → JSON
apiDelete('/items/42')                  // DELETE → JSON (або null при 204)
```

Усі чотири повертають Promise. Для не-2xx відповідей вони reject з `Error`, де `.message` — серверне поле `detail` (або HTTP statusText). **Ніколи не хардкодьте `http://localhost:PORT`** — хелпери використовують відносні шляхи від iframe.

Для системних модулів роутер монтується на `/api/ui/modules/{name}/`, тому:

```
/api/ui/modules/weather-service/widget    ← widget.html
/api/ui/modules/weather-service/settings  ← settings.html
/api/ui/modules/weather-service/data      ← кастомний ендпоінт
```

### Хелпери зворотного зв'язку

```js
showToast('Збережено', 'success');         // зелений — транслюється й у батьківську панель
showToast('Помилка з'єднання', 'error');   // червоний
showToast('Перезапуск…', 'info');          // синій

// Стан завантаження кнопки — вимикає її, показує спінер,
// ловить помилки і показує їх у toast автоматично
withLoading(btnElement, function () {
    return apiPost('/action');
});
```

### Локалізація (i18n) у HTML

Кожен `widget.html` та `settings.html` **повинен** реалізувати вбудовану EN/UK локалізацію. `widget-common.js` надає `LANG`, `t(key)` і `applyLang()` — визначте лише таблицю рядків:

```html
<script>
var L = {
    en: { title: 'Sensor Data', no_data: 'No data', loading: 'Loading…' },
    uk: { title: 'Дані сенсорів', no_data: 'Немає даних', loading: 'Завантаження…' }
};
</script>

<h2 data-i18n="title"></h2>
<p  data-i18n="no_data"></p>
<button data-i18n="refresh"
        data-i18n-title="refresh_tip"
        data-i18n-aria-label="refresh"
        onclick="refresh()"></button>
<input data-placeholder-i18n="ph_search">
```

Підтримувані атрибути:

| Атрибут | Встановлює |
|---|---|
| `data-i18n` | `textContent` |
| `data-placeholder-i18n` | `placeholder` |
| `data-i18n-title` | `title` (підказка) |
| `data-i18n-aria-label` | `aria-label` (екранні зчитувачі) |

Викличте `applyLang()` один раз під час ініціалізації (перед першим викликом `refresh()` / `load()` / `loadStatus()`). Коли користувач змінює мову в батьківській панелі, postMessage `lang_changed` спрацьовує автоматично — `widget-common.js` перезапустить `applyLang()` і викличе вашу функцію `refresh()` / `load()` / `loadStatus()`, якщо вона існує, тож вам не потрібно слухати самостійно.

### PostMessage-події (інформаційно)

`widget-common.js` вже обробляє ці події — слухайте лише якщо вам потрібна додаткова поведінка поверх вбудованої. Канонічні імена подій нижче походять з типизованого протоколу в [src/lib/widgetMessages.ts](../../src/lib/widgetMessages.ts); legacy-аліаси видалено у Phase 5.

| Подія (канонічна) | Напрямок | Вбудований обробник |
|---|---|---|
| `theme_changed` | parent → widget | Перемикає клас `.light` на `<html>` |
| `theme_vars_changed` | parent → widget | Перезавантажує `/api/shared/theme.css` з cache-bust |
| `lang_changed` | parent → widget | Перезапускає `applyLang()` + ваш `refresh`/`load`/`loadStatus` |
| `modal_open` | widget → parent | Відкрити модальне вікно у дашборд-shell |
| `modal_close` | widget → parent | Закрити модальне вікно дашборду |
| `open_settings` | widget → parent | Відкрити панель налаштувань модуля |
| `request_refresh` | widget → parent | Попросити батьківський дашборд перевантажити дані віджета |

> **Видалено у Phase 5:** `openWidgetModal`, `closeWidgetModal`, `openSettings`, `refresh` (legacy-аліаси). Використовуйте канонічні імена вище.

---

## Контракт template-віджета

Для віджетів `kind: "template"` дашборд рендерить віджет нативно з JSON-payload — `widget.html` не поставляється. Модуль експонує data- та action-endpoint-и, які дашборд викликає через проксі `/api/v1/modules/{name}/...`.

**Data-endpoint (читання):**

```
GET /api/v1/modules/{module}/data/{key}
```

Дашборд звертається до payload, оголошеного у `manifest.json` під `ui.widget.data_endpoints[key].path` (відносно mount-point модуля), застосовуючи опціональний `cache_ttl_s`. Форма відповіді залежить від обраного шаблону — див. [dashboard-recraft.md §3.3-3.8](dashboard-recraft.md#33-шаблони).

**Action-endpoint (запис):**

```
POST /api/v1/modules/{module}/action/{key}
Content-Type: application/json

{ "id": "...", "value": ... }
```

Дашборд POSTить на `ui.widget.actions[key].path` модуля. Поширені ключі дій: `toggle` (toggle-list), `set_mode` / `step` (control-panel), `transport` / `volume` (media), `select` (presence). Будь-який 2xx означає успіх; не-2xx показує toast у дашборді.

**Підказки про оновлення:**

`ui.widget.refresh.events` перелічує типи подій EventBus, які мають інвалідувати кеш та одразу рефетчити. `ui.widget.refresh.poll_interval_s` задає fallback-каденцію опитування, коли події не приходять.

### Обов'язкові правила для HTML

```
⛔ Не хардкодити UI-текст жодною мовою — тільки через t('key') або data-i18n
⛔ Не використовувати localhost:PORT — apiGet/apiPost використовують BASE автоматично
✅ Мова читається з localStorage('selena-lang') — значення 'en' | 'uk'
✅ Словники для обох мов (en і uk) повинні містити однаковий набір ключів
✅ applyLang() викликається перед першим refresh()/load()
✅ Скорочення (MQTT, STT, TTS, LLM, ID) та технічні назви не перекладаються
```

---

## 7. Система інтентів — Як додати голосові команди

IntentRouter обробляє голосові та текстові команди через багаторівневу систему маршрутизації:

```
Текст → Tier 1: FastMatcher (ключові слова/regex, ~0 мс)
      → Tier 1.5: IntentCompiler (YAML → компільований regex, мікросекунди)
      → Tier 2: Модулі через Module Bus (мілісекунди)
      → Cache: IntentCache (кеш попередніх результатів LLM, ~0 мс)
      → Tier 3: Локальна LLM (300-800 мс)
      → Tier 4: Хмарна LLM (1-3 с)
      → Fallback: "Вибачте, я не зрозуміла"
```

### Підхід для системних модулів (type: SYSTEM)

Системні модулі декларують свої інтенти у `OWNED_INTENTS` + `_OWNED_INTENT_META` і викликають `_claim_intent_ownership()` зі `start()`. Директорії `config/intents/` і центрального seed-скрипту немає. Повний walkthrough — у [system-module-development.md](system-module-development.md); архітектурний deep dive — у [intent-routing.md](intent-routing.md).

```python
from core.module_loader.system_module import SystemModule

INTENT_DO_ACTION = "mymodule.do_action"
OWNED_INTENTS = [INTENT_DO_ACTION]


class MyModule(SystemModule):
    name = "my-module"

    # Описи завжди англійською — вони потрапляють у LLM-промпт
    _OWNED_INTENT_META = {
        INTENT_DO_ACTION: dict(
            noun_class="DEVICE", verb="set", priority=100,
            description=(
                "Perform some custom action with a freetext argument. "
                "Use when the user asks the module to 'do <something>'."
            ),
        ),
    }

    async def start(self) -> None:
        self.subscribe(["voice.intent"], self._on_voice_intent)
        if self._session_factory is not None:
            await self._claim_intent_ownership()  # див. device_control/module.py для канонічного коду

    async def _on_voice_intent(self, event) -> None:
        payload = event.payload or {}
        if payload.get("intent") != INTENT_DO_ACTION:
            return
        params = payload.get("params") or {}
        target = (params.get("target") or "").strip()
        # ... виконати дію ...
        await self.speak_action(INTENT_DO_ACTION, {
            "result": "ok",
            "target": target,
        })
```

Цього вже достатньо. Жодних FastMatcher-патернів не потрібно — LLM-tier бачить інтент у динамічному каталозі (побудованому з `intent_definitions`) і маршрутизує природньомовні висловлювання будь-якою мовою до нього. Якщо також хочете 0 мс англійський shortcut — додайте рядок у `intent_patterns` з `source='manual'`, `lang='en'`, вашим intent_id і regex.

`speak_action(intent, context)` віддає structured action context до VoiceCore rephrase LLM, який створює природню відповідь мовою TTS користувача.

#### Крок 4 — Озвучення через voice.speak

```python
# Варіант 1: publish + "відпустити" (не чекати завершення)
await self.publish("voice.speak", {"text": "Привіт!"})

# Варіант 2: speak() — чекає завершення TTS
await self.speak("Зачекайте, будь ласка")
# ... код виконується тільки після завершення озвучення
await self.speak("Готово!")
```

### Підхід для користувацьких модулів (type: UI/INTEGRATION/DRIVER/AUTOMATION)

#### Варіант A — Декоратор @intent

```python
from sdk.base_module import SmartHomeModule, intent


class WeatherModule(SmartHomeModule):
    name = "weather-module"
    version = "1.0.0"

    @intent(r"weather|forecast|погода|прогноз",
            name="weather.current",
            description="Current weather query")
    async def handle_weather(self, text: str, context: dict) -> dict:
        weather = await self._fetch_weather()
        return {
            "tts_text": f"Зараз {weather['temp']} градусів, {weather['desc']}",
            "data": weather
        }
```

#### Варіант B — Інтенти в manifest.json

```json
{
    "name": "weather-module",
    "type": "UI",
    "port": 8100,
    "intents": [
        {
            "patterns": {
                "en": ["weather", "forecast", "temperature outside"],
                "uk": ["погода", "прогноз", "температура надворі"]
            },
            "description": "Weather queries",
            "endpoint": "/api/intent"
        }
    ]
}
```

При цьому ядро надсилає `intent` повідомлення через WebSocket, SDK автоматично маршрутизує до обробника.

### Payload події voice.intent

```python
{
    "intent": "media.play_genre",       # назва інтенту
    "response": "",                      # текст для TTS (порожній для system_module)
    "action": None,                      # структурована дія
    "params": {"genre": "jazz"},         # витягнуті параметри з regex named groups
    "source": "system_module",           # "fast_matcher"|"system_module"|"module_bus"|
                                         # "cache"|"llm"|"cloud"|"fallback"
    "user_id": None,                     # ідентифікатор мовця
    "latency_ms": 2,                     # час обробки
    "raw_text": "play jazz radio"        # оригінальний текст користувача
}
```

### Пріоритет інтентів

| Значення | Опис |
|----------|------|
| `priority=10` | Інтенти з витягуванням параметрів (жанр, назва станції, запит) |
| `priority=5` | Прості команди (пауза, стоп, наступний) |
| `order=0-29` | Системні (тільки для вбудованих модулів) |
| `order=30-49` | Ядро |
| `order=50-99` | Користувацькі модулі |

---

## 8. manifest.json — Повний довідник

Файл `manifest.json` — метадані модуля, що перевіряються при встановленні.

### Повна схема

```json
{
    "name": "climate-module",
    "version": "1.0.0",
    "description": "Climate control via Zigbee thermostats",
    "type": "UI",
    "ui_profile": "FULL",
    "api_version": "1.0",
    "runtime_mode": "always_on",
    "port": 8100,
    "room": "home",
    "permissions": [
        "device.read",
        "device.write",
        "events.subscribe",
        "events.publish"
    ],
    "ui": {
        "icon": "icon.svg",
        "widget": {
            "kind": "template",
            "template": "control-panel",
            "size": "2x2",
            "data_endpoints": {
                "state": {"path": "/widget/data/state", "cache_ttl_s": 30}
            },
            "actions": {
                "set_mode": {"path": "/widget/action/set_mode"},
                "step": {"path": "/widget/action/step"}
            },
            "refresh": {"events": ["climate.changed"]}
        },
        "settings": "settings.html"
    },
    "intents": [
        {
            "patterns": {
                "en": ["climate", "temperature"],
                "uk": ["клімат", "температура"]
            },
            "description": "Climate control commands"
        }
    ],
    "oauth": null,
    "resources": {
        "memory_mb": 128,
        "cpu": 0.25
    },
    "author": "SmartHome LK",
    "license": "MIT",
    "homepage": "https://github.com/dotradepro/SelenaCore"
}
```

> **`kind: "template"` — переважний шлях.** Дашборд рендерить віджет нативно з JSON-payload; `widget.html` потрібен лише для `kind: "custom"`. Повну довідку по 8 шаблонах — див. [widget-development.md](widget-development.md) та [dashboard-recraft.md §3.3-3.8](dashboard-recraft.md#33-шаблони).

### Обов'язкові поля

| Поле | Тип | Опис |
|------|-----|------|
| `name` | `string` | Унікальна назва модуля (slug формат: `my-module`). |
| `version` | `string` | Версія у форматі semver: `MAJOR.MINOR.PATCH`. |
| `type` | `string` | Тип модуля (див. таблицю нижче). |
| `api_version` | `string` | Версія Core API: `"1.0"`. |
| `port` | `integer` | Порт для прослуховування (тільки для USER модулів). |
| `room` | `string` | **Тільки для UI-модулів.** Тег кімнати — керує room-табами дашборду. `"system"`, `"home"` або власна назва. |
| `permissions` | `string[]` | Список необхідних дозволів. |

### Необов'язкові поля

| Поле | Тип | За замовчуванням | Опис |
|------|-----|------------------|------|
| `description` | `string` | `""` | Опис модуля. |
| `ui_profile` | `string` | `"HEADLESS"` | Профіль UI. |
| `runtime_mode` | `string` | `"always_on"` | Режим запуску. |
| `ui` | `object` | `null` | Налаштування UI: `{icon, widget, settings}`. `widget` — це або `{kind: "template", template, size, data_endpoints, actions?, refresh?}` (переважний), або `{kind: "custom", file, size}` (legacy iframe). |
| `intents` | `array` | `[]` | Оголошені інтенти (для USER модулів). |
| `oauth` | `object` | `null` | Конфігурація OAuth. |
| `resources` | `object` | `null` | Обмеження ресурсів. |
| `author` | `string` | `""` | Автор. |
| `license` | `string` | `""` | Ліцензія. |
| `homepage` | `string` | `""` | URL домашньої сторінки. |

### Допустимі значення

**Типи модулів (`type`):**

| Значення | Опис | Контейнер | Порт |
|----------|------|-----------|------|
| `SYSTEM` | Системний модуль (in-process) | Ні | Ні |
| `UI` | Модуль з повним UI | Так | Так |
| `INTEGRATION` | Інтеграція з зовнішнім API | Так | Так |
| `DRIVER` | Драйвер пристрою | Так | Так |
| `AUTOMATION` | Модуль автоматизації | Так | Так |
| `IMPORT_SOURCE` | Імпорт з іншої платформи | Так | Так |

**UI профілі (`ui_profile`):**

| Значення | Опис |
|----------|------|
| `HEADLESS` | Без UI — тільки API та фоновий процес |
| `SETTINGS_ONLY` | Тільки сторінка налаштувань |
| `ICON_SETTINGS` | Іконка на дашборді + налаштування |
| `FULL` | Повний UI: іконка + віджет + налаштування |

**Режими запуску (`runtime_mode`):**

| Значення | Опис |
|----------|------|
| `always_on` | Завжди запущений |
| `on_demand` | Запускається за запитом |
| `scheduled` | Запускається за розкладом |

**Дозволи (`permissions`):**

| Дозвіл | Опис |
|--------|------|
| `device.read` | Читання пристроїв з реєстру |
| `device.write` | Запис/оновлення стану пристроїв |
| `events.subscribe` | Підписка на події EventBus |
| `events.publish` | Публікація подій в EventBus |
| `secrets.oauth` | Доступ до OAuth-потоку (тільки `INTEGRATION`) |
| `secrets.proxy` | API-проксі через Secrets Vault (тільки `INTEGRATION`) |

### Правила для SYSTEM модулів

```json
{
    "name": "my-system-module",
    "type": "SYSTEM",
    "version": "1.0.0",
    "api_version": "1.0",
    "runtime_mode": "always_on",
    "permissions": ["events.publish", "events.subscribe"]
}
```

```
⛔ НЕ вказувати поле "port" для SYSTEM модулів
⛔ SYSTEM модулі не запускаються як окремі процеси/контейнери
✅ Порти потрібні тільки для USER модулів
```

### Валідація при встановленні

```python
REQUIRED_FIELDS = ["name", "version", "type", "api_version", "port", "permissions"]
VALID_TYPES = ["SYSTEM", "UI", "INTEGRATION", "DRIVER", "AUTOMATION", "IMPORT_SOURCE"]
VALID_PROFILES = ["HEADLESS", "SETTINGS_ONLY", "ICON_SETTINGS", "FULL"]
VALID_RUNTIME = ["always_on", "on_demand", "scheduled"]
VERSION_PATTERN = r"^\d+\.\d+\.\d+$"  # semver
```

---

## 9. Повні приклади

### Приклад 1: Системний модуль — агрегатор даних сенсорів

Збирає дані з усіх зареєстрованих сенсорів та надає API для отримання агрегованих метрик.

**Структура файлів:**

```
system_modules/sensor_aggregator/
    __init__.py
    module.py
    aggregator.py
    manifest.json
    widget.html
    settings.html
```

**`__init__.py`:**

```python
from .module import SensorAggregatorModule as module_class  # noqa: F401
```

**`manifest.json`:**

```json
{
    "name": "sensor-aggregator",
    "type": "SYSTEM",
    "version": "1.0.0",
    "api_version": "1.0",
    "runtime_mode": "always_on",
    "description": "Aggregates sensor data and provides metrics API",
    "permissions": ["device.read", "events.subscribe", "events.publish"]
}
```

**`module.py`:**

```python
import logging
from fastapi import APIRouter
from core.module_loader.system_module import SystemModule
from .aggregator import SensorAggregator

logger = logging.getLogger(__name__)


class SensorAggregatorModule(SystemModule):
    name = "sensor-aggregator"

    def __init__(self) -> None:
        super().__init__()
        self._aggregator = SensorAggregator()

    async def start(self) -> None:
        self.subscribe(["device.state_changed"], self._on_state_changed)
        self.subscribe(["device.registered"], self._on_device_registered)

        # Завантаження початкових даних
        devices = await self.fetch_devices()
        for dev in devices:
            if dev["type"] == "sensor":
                self._aggregator.add_reading(dev["device_id"], dev["state"])

        logger.info("SensorAggregator started with %d devices", len(devices))

    async def stop(self) -> None:
        self._cleanup_subscriptions()
        logger.info("SensorAggregator stopped")

    def get_router(self) -> APIRouter:
        router = APIRouter()
        agg = self._aggregator

        @router.get("/metrics")
        async def get_metrics() -> dict:
            return agg.get_all_metrics()

        @router.get("/metrics/{device_id}")
        async def get_device_metrics(device_id: str) -> dict:
            return agg.get_device_metrics(device_id)

        self._register_html_routes(router, __file__)
        self._register_health_endpoint(router)
        return router

    async def _on_state_changed(self, event) -> None:
        payload = event.payload
        device_id = payload.get("device_id", "")
        new_state = payload.get("new_state", {})
        self._aggregator.add_reading(device_id, new_state)

    async def _on_device_registered(self, event) -> None:
        payload = event.payload
        if payload.get("type") == "sensor":
            logger.info("New sensor registered: %s", payload.get("device_id"))
```

**`aggregator.py`:**

```python
from collections import defaultdict
from typing import Any


class SensorAggregator:
    def __init__(self, max_readings: int = 1000) -> None:
        self._readings: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._max_readings = max_readings

    def add_reading(self, device_id: str, state: dict[str, Any]) -> None:
        readings = self._readings[device_id]
        readings.append(state)
        if len(readings) > self._max_readings:
            self._readings[device_id] = readings[-self._max_readings:]

    def get_device_metrics(self, device_id: str) -> dict[str, Any]:
        readings = self._readings.get(device_id, [])
        if not readings:
            return {"device_id": device_id, "count": 0}
        return {
            "device_id": device_id,
            "count": len(readings),
            "latest": readings[-1],
        }

    def get_all_metrics(self) -> dict[str, Any]:
        return {
            "total_devices": len(self._readings),
            "total_readings": sum(len(r) for r in self._readings.values()),
            "devices": {
                did: self.get_device_metrics(did)
                for did in self._readings
            },
        }
```

### Приклад 2: Користувацький модуль — контролер розумної розетки

Керує розумними розетками через EventBus та надає голосові команди.

**Структура файлів:**

```
smart_plug_controller/
    main.py
    manifest.json
    locales/
        en.json
        uk.json
```

**`manifest.json`:**

```json
{
    "name": "smart-plug-controller",
    "type": "DRIVER",
    "version": "1.0.0",
    "api_version": "1.0",
    "runtime_mode": "always_on",
    "port": 8120,
    "permissions": ["device.read", "device.write", "events.subscribe", "events.publish"],
    "intents": [
        {
            "patterns": {
                "en": ["turn (on|off) (?:the )?plug", "plug (on|off)"],
                "uk": ["(увімкни|вимкни) розетку", "розетка (увімкни|вимкни)"]
            },
            "priority": 50,
            "name": "plug.toggle",
            "description": "Turn smart plug on or off"
        }
    ]
}
```

**`locales/en.json`:**

```json
{
    "plug_on": "Smart plug turned on",
    "plug_off": "Smart plug turned off",
    "plug_not_found": "Smart plug not found",
    "status_check": "Plug is currently {state}"
}
```

**`locales/uk.json`:**

```json
{
    "plug_on": "Розумну розетку увімкнено",
    "plug_off": "Розумну розетку вимкнено",
    "plug_not_found": "Розумну розетку не знайдено",
    "status_check": "Розетка зараз {state}"
}
```

**`main.py`:**

```python
import asyncio
import re
from sdk.base_module import SmartHomeModule, intent, on_event, scheduled


class SmartPlugController(SmartHomeModule):
    name = "smart-plug-controller"
    version = "1.0.0"

    def __init__(self) -> None:
        super().__init__()
        self._plug_device_id: str | None = None

    async def on_start(self) -> None:
        self._log.info("SmartPlugController initializing")

    async def on_stop(self) -> None:
        self._log.info("SmartPlugController stopped")

    @intent(r"(?:turn\s+)?(on|off)\s+(?:the\s+)?plug|plug\s+(on|off)|"
            r"(увімкни|вимкни)\s+розетку|розетк[уа]\s+(увімкни|вимкни)",
            name="plug.toggle",
            description="Toggle smart plug on/off")
    async def handle_toggle(self, text: str, context: dict) -> dict:
        lang = context.get("_lang", "en")

        # Визначення бажаного стану
        text_lower = text.lower()
        turn_on = any(w in text_lower for w in ["on", "увімкни"])

        if not self._plug_device_id:
            return {"tts_text": self.t("plug_not_found", lang=lang)}

        # Оновлення стану через Core API
        try:
            await self.api_request(
                "PATCH",
                f"/devices/{self._plug_device_id}/state",
                body={"state": {"on": turn_on}}
            )
        except Exception as exc:
            self._log.error("Failed to toggle plug: %s", exc)
            return {"tts_text": self.t("plug_not_found", lang=lang)}

        key = "plug_on" if turn_on else "plug_off"
        return {"tts_text": self.t(key, lang=lang)}

    @on_event("device.registered")
    async def on_device_registered(self, data: dict) -> None:
        if data.get("type") == "actuator" and "plug" in data.get("name", "").lower():
            self._plug_device_id = data.get("device_id")
            self._log.info("Found smart plug: %s", self._plug_device_id)

    @scheduled("every:5m")
    async def heartbeat(self) -> None:
        if self._plug_device_id:
            device = await self.get_device(self._plug_device_id)
            if device:
                self._log.debug("Plug status: %s", device.get("state", {}))


if __name__ == "__main__":
    module = SmartPlugController()
    asyncio.run(module.start())
```

### Приклад 3: Інтеграційний модуль — міст до зовнішнього API

Отримує дані з зовнішнього API погоди та публікує їх як події.

**Структура файлів:**

```
weather_bridge/
    main.py
    manifest.json
    locales/
        en.json
        uk.json
```

**`manifest.json`:**

```json
{
    "name": "weather-bridge",
    "type": "INTEGRATION",
    "version": "1.0.0",
    "api_version": "1.0",
    "runtime_mode": "always_on",
    "port": 8130,
    "permissions": [
        "events.publish",
        "events.subscribe",
        "secrets.proxy"
    ],
    "intents": [
        {
            "patterns": {
                "en": ["weather", "forecast", "temperature outside", "how.* outside"],
                "uk": ["погода", "прогноз", "температура надворі", "що надворі"]
            },
            "priority": 50,
            "name": "weather.current",
            "description": "Get current weather conditions"
        }
    ],
    "ui": {
        "icon": "icon.svg",
        "widget": {
            "file": "widget.html",
            "size": "2x1"
        },
        "settings": "settings.html"
    }
}
```

**`locales/en.json`:**

```json
{
    "weather_report": "Currently {temp} degrees, {desc}",
    "weather_unavailable": "Weather data is currently unavailable",
    "fetching": "Checking the weather..."
}
```

**`locales/uk.json`:**

```json
{
    "weather_report": "Зараз {temp} градусів, {desc}",
    "weather_unavailable": "Дані про погоду наразі недоступні",
    "fetching": "Перевіряю погоду..."
}
```

**`main.py`:**

```python
import asyncio
from sdk.base_module import SmartHomeModule, intent, scheduled


class WeatherBridge(SmartHomeModule):
    name = "weather-bridge"
    version = "1.0.0"

    def __init__(self) -> None:
        super().__init__()
        self._cached_weather: dict | None = None

    async def on_start(self) -> None:
        self._log.info("WeatherBridge starting")

    async def on_stop(self) -> None:
        self._log.info("WeatherBridge stopped")

    async def _fetch_weather(self) -> dict | None:
        """Отримання погоди через Secrets Proxy (токен не видний модулю)."""
        try:
            result = await self.api_request(
                "POST", "/secrets/proxy",
                body={
                    "module": self.name,
                    "url": "https://api.openweathermap.org/data/2.5/weather?q=Kyiv&units=metric",
                    "method": "GET",
                    "headers": {},
                    "body": None
                },
                timeout=15.0
            )
            body = result.get("body", {})
            if "main" in body:
                weather = {
                    "temp": round(body["main"]["temp"]),
                    "desc": body["weather"][0]["description"] if body.get("weather") else "unknown",
                    "humidity": body["main"].get("humidity", 0),
                }
                self._cached_weather = weather
                return weather
        except Exception as exc:
            self._log.error("Weather fetch failed: %s", exc)
        return self._cached_weather

    @intent(r"weather|forecast|погода|прогноз|температура надворі|що надворі",
            name="weather.current",
            description="Current weather conditions")
    async def handle_weather(self, text: str, context: dict) -> dict:
        lang = context.get("_lang", "en")
        weather = await self._fetch_weather()

        if not weather:
            return {"tts_text": self.t("weather_unavailable", lang=lang)}

        return {
            "tts_text": self.t("weather_report", lang=lang,
                               temp=weather["temp"], desc=weather["desc"]),
            "data": weather
        }

    @scheduled("every:30m")
    async def update_weather(self) -> None:
        """Оновлення кешу погоди кожні 30 хвилин."""
        weather = await self._fetch_weather()
        if weather:
            await self.publish_event("weather.updated", weather)
            self._log.info("Weather updated: %s", weather)

    async def handle_api_request(self, method: str, path: str, body) -> dict:
        """Обробка API-запитів від UI (через Module Bus проксі)."""
        if method == "GET" and path == "/current":
            weather = await self._fetch_weather()
            if weather:
                return weather
            return {"error": "No weather data available"}
        return {"error": f"Not implemented: {method} {path}"}


if __name__ == "__main__":
    module = WeatherBridge()
    asyncio.run(module.start())
```

---

## Додаткові ресурси

- [Архітектура системи](architecture.md) — загальний огляд архітектури SelenaCore
- [Довідник протоколу WebSocket Module Bus](module-bus-protocol.md) — детальний опис протоколу
- [Розробка системних модулів](system-module-development.md) — поглиблений посібник
- [Розробка віджетів](widget-development.md) — гід по widget.html та settings.html
- [API Reference](api-reference.md) — повний довідник Core API

---

*SelenaCore Module API Guide (UK) -- SmartHome LK -- Open Source MIT*
*Репозиторій: https://github.com/dotradepro/SelenaCore*
