# Посібник з розробки системних модулів

Цей посібник охоплює все, що потрібно для створення, реєстрації та підтримки **системного модуля** для SelenaCore. Системні модулі працюють всередині процесу ядра та мають прямий доступ до EventBus, бази даних та застосунку FastAPI -- без контейнерів, без мережевих затримок.

---

## Зміст

1. [Що таке системні модулі](#що-таке-системні-модулі)
2. [Огляд архітектури](#огляд-архітектури)
3. [Структура модуля](#структура-модуля)
4. [Довідка по базовому класу](#довідка-по-базовому-класу)
5. [Інтеграція з EventBus](#інтеграція-з-eventbus)
6. [Доступ до реєстру пристроїв](#доступ-до-реєстру-пристроїв)
7. [Додавання REST API](#додавання-rest-api)
8. [Інтеграція з IntentRouter](#інтеграція-з-intentrouter)
9. [Процес завантаження](#процес-завантаження)
10. [Повний приклад](#повний-приклад)
11. [Системні модулі проти користувацьких](#системні-модулі-проти-користувацьких)
12. [Вбудовані системні модулі](#вбудовані-системні-модулі)
13. [Найкращі практики](#найкращі-практики)
14. [Усунення несправностей](#усунення-несправностей)

---

## Що таке системні модулі

Системні модулі -- це Python-пакети, що працюють **всередині** процесу SelenaCore. Вони завантажуються через `importlib` при запуску та взаємодіють з рештою системи через прямі виклики Python -- без Docker-контейнерів, без серіалізації WebSocket, без мережевих переходів.

Ключові характеристики:

- **Виконання в процесі** через Python `importlib`
- **~0 МБ додаткової RAM** -- без накладних витрат контейнера
- **Прямий доступ до EventBus** через асинхронні зворотні виклики
- **Прямий доступ до бази даних** через спільну фабрику асинхронних сесій SQLAlchemy
- **Необов'язковий FastAPI-роутер**, що монтується на `/api/ui/modules/{name}/`
- Розташовані у каталозі `system_modules/`
- Наразі **22 вбудовані** системні модулі поставляються з SelenaCore

Використовуйте системний модуль, коли потрібна тісна інтеграція з ядром, низька затримка або прямий доступ до бази даних. Використовуйте [користувацький модуль](user-module-development.md), коли потрібна ізоляція, незалежне розгортання або розширюваність третіми сторонами.

---

## Огляд архітектури

```
SelenaCore Process
 |
 +-- PluginManager
 |     +-- scan_local_modules()      # виявляє system_modules/*
 |     +-- validate manifest.json
 |     +-- importlib.import_module()
 |
 +-- EventBus (in-process)
 |     +-- DirectSubscription        # асинхронний зворотній виклик, без серіалізації
 |
 +-- SQLAlchemy async session
 |     +-- async_sessionmaker         # впроваджується через setup()
 |
 +-- FastAPI app
       +-- /api/ui/modules/{name}/   # необов'язковий роутер для кожного модуля
```

Кожен системний модуль отримує дві основні залежності через `setup()`:

1. **EventBus** -- публікація та підписка на події через асинхронні зворотні виклики.
2. **async_sessionmaker** -- створення сесій бази даних для прямих SQL-запитів.

Вони впроваджуються автоматично завантажувачем перед викликом `start()`.

---

## Структура модуля

Кожен системний модуль знаходиться у власному пакеті всередині `system_modules/`:

```
system_modules/my_module/
    __init__.py      # Повинен експортувати: module_class = MyModule
    module.py        # Підклас SystemModule з логікою start/stop
    manifest.json    # Метадані модуля; type повинен бути "SYSTEM"
```

### `__init__.py`

Файл `__init__.py` повинен експортувати єдине ім'я: `module_class`. Це клас, який завантажувач буде інстанціювати.

```python
from .module import MyModule as module_class
```

### `manifest.json`

```json
{
    "name": "my-module",
    "version": "1.0.0",
    "type": "SYSTEM",
    "runtime_mode": "always_on",
    "permissions": []
}
```

| Поле | Обов'язкове | Опис |
|---|---|---|
| `name` | Так | Унікальний ідентифікатор. Повинен збігатися з `SystemModule.name` у вашому класі. Використовуйте малі літери з дефісами (kebab-case). |
| `version` | Так | Рядок семантичної версії. |
| `type` | Так | Повинен бути `"SYSTEM"` для системних модулів. |
| `runtime_mode` | Так | `"always_on"` (запускається при завантаженні) або `"on_demand"` (запускається за потребою). |
| `permissions` | Ні | Список рядків дозволів, які потребує модуль (наприклад, `["devices.read", "devices.write"]`). |

Системні модулі **не** вказують поле `port`. Вони використовують спільний процес ядра і, за потреби, монтують FastAPI-роутер.

### `module.py`

Містить ваш підклас `SystemModule`. Див. [довідку по базовому класу](#довідка-по-базовому-класу) та [повний приклад](#повний-приклад) нижче.

---

## Довідка по базовому класу

Усі системні модулі наслідуються від `SystemModule`, визначеного у `core/module_loader/system_module.py`.

```python
from abc import ABC, abstractmethod
from typing import Any, Callable

class SystemModule(ABC):
    name: str  # Повинен збігатися з "name" у manifest.json

    def setup(self, bus: EventBus, session_factory: async_sessionmaker) -> None:
        """Впроваджується завантажувачем перед start().
        Зберігає посилання на EventBus та фабрику сесій бази даних.
        НЕ перевизначайте це, якщо не викликаєте super().setup(...) першим."""

    @abstractmethod
    async def start(self) -> None:
        """Викликається після setup(). Ініціалізуйте ваш сервіс, підпишіться на події,
        запустіть фонові задачі."""

    @abstractmethod
    async def stop(self) -> None:
        """Викликається під час завершення роботи. Скасуйте фонові задачі, звільніть ресурси,
        відпишіться від EventBus."""

    def get_router(self) -> APIRouter | None:
        """Повертає FastAPI APIRouter для монтування на
        /api/ui/modules/{name}/. Поверніть None, якщо API не потрібен."""
        return None
```

### Життєвий цикл

```
__init__()  -->  setup(bus, session_factory)  -->  start()
                                                      |
                                               (модуль працює)
                                                      |
                                                   stop()
```

1. Завантажувач створює екземпляр вашого класу через `module_class()`.
2. `setup()` впроваджує EventBus та фабрику сесій бази даних.
3. Викликається `start()` -- ваш модуль тепер активний.
4. При завершенні роботи (або перезавантаженні модуля) викликається `stop()`.

---

## Інтеграція з EventBus

Системні модулі взаємодіють з EventBus через допоміжні методи, успадковані від `SystemModule`. Оскільки системні модулі працюють в процесі, доставка подій -- це прямий асинхронний зворотний виклик -- без серіалізації, без мережевих затримок.

### Підписка на події

```python
async def start(self) -> None:
    self.subscribe(
        event_types=["device.state_changed", "device.online"],
        callback=self._on_device_event
    )
```

Метод `subscribe()` повертає ідентифікатор підписки та реєструє асинхронний зворотний виклик. Сигнатура зворотного виклику:

```python
async def _on_device_event(self, event: Event) -> None:
    device_id = event.payload.get("device_id")
    new_state = event.payload.get("state")
    # Обробка події...
```

Ви можете підписатися на декілька типів подій одним викликом або зробити окремі виклики `subscribe()` для різних обробників.

### Публікація подій

```python
await self.publish("module.started", {"name": self.name})
await self.publish("device.command", {
    "device_id": "light-001",
    "command": "turn_on",
    "params": {"brightness": 80}
})
```

Перший аргумент -- рядок типу події. Другий -- словник даних.

### Відписка

Завжди очищуйте підписки при зупинці модуля:

```python
async def stop(self) -> None:
    self._cleanup_subscriptions()
```

Допоміжний метод `_cleanup_subscriptions()` видаляє всі підписки, зареєстровані цим екземпляром модуля.

### Типові типи подій

| Тип події | Дані | Опис |
|---|---|---|
| `device.state_changed` | `{device_id, state, previous_state}` | Пристрій змінив стан |
| `device.online` | `{device_id}` | Пристрій з'явився в мережі |
| `device.offline` | `{device_id}` | Пристрій зник з мережі |
| `device.protocol_heartbeat` | `{device_id, protocol, timestamp}` | Heartbeat від протокольного моста |
| `device.command` | `{device_id, command, params}` | Команда, надіслана пристрою |
| `module.started` | `{name}` | Модуль завершив запуск |
| `module.stopped` | `{name}` | Модуль зупинився |
| `automation.triggered` | `{rule_id, trigger}` | Спрацювало правило автоматизації |

---

## Доступ до реєстру пристроїв

Системні модулі мають прямий доступ до бази даних через допоміжні методи. Вони обгортають SQLAlchemy-запити за чистим асинхронним інтерфейсом.

### Отримання всіх пристроїв

```python
devices = await self.fetch_devices()  # Повертає list[dict]
for device in devices:
    print(device["id"], device["name"], device["type"])
```

### Отримання стану пристрою

```python
state = await self.get_device_state(device_id)
# Повертає dict, наприклад {"power": True, "brightness": 80, "color_temp": 4000}
```

### Оновлення стану пристрою

```python
await self.patch_device_state(device_id, {"power": True, "brightness": 80})
```

Це об'єднує надані поля з існуючим станом. Поля, які не включені, залишаються без змін.

### Реєстрація нового пристрою

```python
device_id = await self.register_device(
    name="Kitchen Light",
    type="actuator",          # sensor | actuator | controller | virtual
    protocol="zigbee",
    capabilities=["turn_on", "turn_off", "set_brightness"],
    meta={"manufacturer": "IKEA", "model": "TRADFRI"}
)
```

**Типи пристроїв:**

| Тип | Опис |
|---|---|
| `sensor` | Повідомляє вимірювання (температура, вологість, рух) |
| `actuator` | Виконує дії (світло, перемикачі, замки) |
| `controller` | Надсилає команди (пульти, кнопки, настінні перемикачі) |
| `virtual` | Програмно визначений пристрій (таймери, обчислені значення) |

---

## Додавання REST API

Перевизначте `get_router()` для відкриття HTTP-ендпоінтів. Повернутий роутер монтується на `/api/ui/modules/{name}/`, тому маршрут, визначений як `/health`, стає `/api/ui/modules/my-module/health`.

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

class BrightnessRequest(BaseModel):
    device_id: str
    brightness: int

class MyModule(SystemModule):
    name = "my-module"

    def get_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/health")
        async def health():
            return {"status": "ok", "name": self.name}

        @router.get("/devices")
        async def list_devices():
            devices = await self.fetch_devices()
            return {"devices": devices, "count": len(devices)}

        @router.post("/brightness")
        async def set_brightness(req: BrightnessRequest):
            if not 0 <= req.brightness <= 100:
                raise HTTPException(400, "Brightness must be 0-100")
            await self.patch_device_state(
                req.device_id, {"brightness": req.brightness}
            )
            return {"ok": True}

        return router
```

Поради щодо REST API:

- Використовуйте Pydantic-моделі для валідації запитів.
- Використовуйте `HTTPException` для відповідей з помилками.
- Робіть шляхи маршрутів короткими -- ім'я модуля вже є у префіксі URL.
- Повертайте словники, що серіалізуються у JSON, або Pydantic-моделі.

---

## Інтеграція з IntentRouter

Системні модулі можуть реєструвати наміри для **Tier 1.5** (обробка намірів в процесі). Це дозволяє LLM-двигуну маршрутизувати команди природною мовою безпосередньо до вашого модуля без проходження через повний LLM-конвеєр.

```python
from system_modules.llm_engine.intent_router import register_system_intent

async def _handle_light_command(text: str, context: dict) -> dict:
    # Розбір та виконання команди
    return {"response": "Light turned on", "action": "turn_on"}

register_system_intent(
    pattern=r"turn (on|off) the (?P<room>\w+) light",
    handler=_handle_light_command,
    priority=15
)
```

**Діапазони пріоритетів для системних намірів: 0-29.** Менші числа зіставляються першими. Обирайте пріоритет залежно від специфічності вашого шаблону:

| Пріоритет | Випадок використання |
|---|---|
| 0-9 | Точні збіги команд, критичні для безпеки наміри |
| 10-19 | Загальне керування пристроями, типові запити |
| 20-29 | Резервні шаблони, широкі збіги |

---

## Процес завантаження

Розуміння послідовності завантаження допомагає з налагодженням та визначенням моменту виконання вашого коду:

1. **Виявлення** -- `PluginManager.scan_local_modules()` обходить `system_modules/` та знаходить каталоги, що містять `manifest.json`.
2. **Валідація** -- Маніфест аналізується та перевіряється. `type` повинен бути `"SYSTEM"`.
3. **Імпорт** -- `importlib.import_module(f"system_modules.{name}")` завантажує пакет.
4. **Отримання класу** -- Завантажувач зчитує `module_class` з `__init__.py` пакету.
5. **Інстанціювання** -- `instance = module_class()`.
6. **Впровадження** -- `instance.setup(bus, session_factory)` надає доступ до EventBus та бази даних.
7. **Запуск** -- Для модулів `"always_on"` негайно викликається `instance.start()`.
8. **Монтування роутера** -- Якщо `get_router()` повертає не-None роутер, він монтується на `/api/ui/modules/{name}/`.

Якщо будь-який крок не вдається, помилка логується, і модуль пропускається -- інші модулі продовжують завантажуватися нормально.

---

## Повний приклад

Нижче наведено повний робочий системний модуль, що моніторить рівень заряду батареї пристроїв та надсилає сповіщення при низькому заряді.

### `system_modules/battery_monitor/__init__.py`

```python
from .module import BatteryMonitorModule as module_class
```

### `system_modules/battery_monitor/manifest.json`

```json
{
    "name": "battery-monitor",
    "version": "1.0.0",
    "type": "SYSTEM",
    "runtime_mode": "always_on",
    "permissions": ["devices.read"]
}
```

### `system_modules/battery_monitor/module.py`

```python
import asyncio
import logging
from fastapi import APIRouter
from core.module_loader.system_module import SystemModule

logger = logging.getLogger(__name__)

LOW_BATTERY_THRESHOLD = 20  # percent
CHECK_INTERVAL = 3600       # seconds (1 hour)


class BatteryMonitorModule(SystemModule):
    name = "battery-monitor"

    def __init__(self) -> None:
        super().__init__()
        self._check_task: asyncio.Task | None = None
        self._low_battery_devices: dict[str, int] = {}

    async def start(self) -> None:
        # Subscribe to state changes so we catch battery updates in real time
        self.subscribe(
            event_types=["device.state_changed"],
            callback=self._on_state_changed,
        )

        # Also run a periodic full scan
        self._check_task = asyncio.create_task(self._periodic_check())

        await self.publish("module.started", {"name": self.name})
        logger.info("Battery monitor started (threshold=%d%%)", LOW_BATTERY_THRESHOLD)

    async def stop(self) -> None:
        if self._check_task and not self._check_task.done():
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass

        self._cleanup_subscriptions()
        logger.info("Battery monitor stopped")

    # ---- Event handler ----

    async def _on_state_changed(self, event) -> None:
        payload = event.payload
        device_id = payload.get("device_id")
        state = payload.get("state", {})
        battery = state.get("battery_level")

        if battery is None:
            return

        if battery < LOW_BATTERY_THRESHOLD:
            if device_id not in self._low_battery_devices:
                self._low_battery_devices[device_id] = battery
                await self.publish("notification.send", {
                    "title": "Low Battery",
                    "body": f"Device {device_id} battery is at {battery}%",
                    "priority": "warning",
                })
                logger.warning("Low battery: %s at %d%%", device_id, battery)
        else:
            self._low_battery_devices.pop(device_id, None)

    # ---- Background task ----

    async def _periodic_check(self) -> None:
        while True:
            try:
                devices = await self.fetch_devices()
                for device in devices:
                    state = await self.get_device_state(device["id"])
                    battery = state.get("battery_level")
                    if battery is not None and battery < LOW_BATTERY_THRESHOLD:
                        self._low_battery_devices[device["id"]] = battery
            except Exception:
                logger.exception("Error during periodic battery check")

            await asyncio.sleep(CHECK_INTERVAL)

    # ---- REST API ----

    def get_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/health")
        async def health():
            return {"status": "ok", "name": self.name}

        @router.get("/low-battery")
        async def low_battery():
            return {
                "threshold": LOW_BATTERY_THRESHOLD,
                "devices": self._low_battery_devices,
                "count": len(self._low_battery_devices),
            }

        return router
```

Після розміщення у `system_modules/battery_monitor/`, SelenaCore підхоплює його при наступному перезапуску. API стає доступним за адресами:

- `GET /api/ui/modules/battery-monitor/health`
- `GET /api/ui/modules/battery-monitor/low-battery`

---

## Системні модулі проти користувацьких

| Характеристика | Системний модуль | Користувацький модуль |
|---|---|---|
| **Виконання** | В процесі (`importlib`) | Docker-контейнер |
| **Комунікація** | Прямі виклики Python | WebSocket Module Bus |
| **Базовий клас** | `SystemModule` | `SmartHomeModule` |
| **EventBus** | DirectSubscription (асинхронний зворотний виклик) | Доставка через Module Bus (серіалізована) |
| **База даних** | Пряма сесія SQLAlchemy | Через API-проксі |
| **REST API** | Необов'язковий `get_router()` | `handle_api_request()` |
| **Витрати RAM** | ~0 МБ | Накладні витрати контейнера |
| **Порт** | Не потрібен | Не потрібен (шина) |
| **Ізоляція** | Спільний процес ядра | Повна ізоляція |
| **Вплив збою** | Може вплинути на ядро | Обмежений контейнером |
| **Гаряче перезавантаження** | Потребує перезапуску ядра | Незалежний перезапуск |

**Обирайте системний модуль, коли:**

- Потрібна обробка подій за частки мілісекунди.
- Потрібні прямі запити до бази даних.
- Модуль тісно пов'язаний з функціональністю ядра.
- RAM обмежена (наприклад, Raspberry Pi з обмеженою пам'яттю).

**Обирайте користувацький модуль, коли:**

- Потрібна ізоляція збоїв -- збій не повинен зупиняти ядро.
- Модуль створений спільнотою або третьою стороною.
- Потрібна незалежна версіонізація та розгортання.
- Модуль має важкі залежності, які не повинні обтяжувати ядро.

---

## Вбудовані системні модулі

SelenaCore поставляється з 22 системними модулями:

| Модуль | Опис |
|---|---|
| `voice_core` | STT (Vosk), TTS (Piper), розпізнавання слова активації |
| `llm_engine` | LLM-клієнт Ollama, маршрутизатор намірів, швидке зіставлення |
| `ui_core` | Веб-сервер панелі керування (:80) |
| `user_manager` | Профілі користувачів, автентифікація, біометрія |
| `automation_engine` | YAML-двигун правил для автоматизацій |
| `scheduler` | Cron, інтервальне та сонячне планування задач |
| `device_watchdog` | Моніторинг стану пристроїв, виявлення офлайну |
| `protocol_bridge` | Протокольні мости MQTT та Home Assistant |
| `notification_router` | Багатоканальні сповіщення (push, email) |
| `media_player` | Відтворення аудіо з VLC |
| `presence_detection` | Відстеження присутності через WiFi/BLE |
| `hw_monitor` | Моніторинг CPU, RAM, диска та температури |
| `backup_manager` | Локальне та хмарне резервне копіювання |
| `remote_access` | Інтеграція з Tailscale VPN |
| `network_scanner` | Виявлення мережевих пристроїв (ARP, mDNS, SSDP) |
| `import_adapters` | Імпорт пристроїв з Home Assistant, Tuya, Hue |
| `energy_monitor` | Відстеження енергоспоживання |
| `update_manager` | Оновлення ядра та модулів |
| `notify_push` | Web Push VAPID-сповіщення |
| `secrets_vault` | Зашифроване сховище токенів AES-256-GCM |
| `weather_service` | Інтеграція з API погоди |

22-й модуль тут не перелічений, оскільки є внутрішньою утилітою ядра. Перегляньте `system_modules/` для повного набору.

---

## Найкращі практики

### Запуск

- Тримайте `start()` швидким. Якщо потрібна важка ініціалізація, створіть фонову задачу `asyncio.Task` та поверніть керування негайно.
- Завжди публікуйте `module.started` наприкінці `start()`, щоб інші модулі могли на це покладатися.

### Завершення роботи

- **Завжди** викликайте `self._cleanup_subscriptions()` у `stop()`. Витік підписок спричиняє витоки пам'яті та фантомну обробку подій.
- Скасовуйте всі фонові екземпляри `asyncio.Task` та очікуйте їх з обробником `CancelledError`.
- Звільняйте будь-які файлові дескриптори, сокети або зовнішні з'єднання.

### Обробка помилок

- Обгортайте фонові цикли у `try/except`, щоб одна помилка не вбивала задачу.
- Логуйте винятки через `logger.exception()` для збереження повних трасувань стеку.
- Ніколи не дозволяйте виняткам виходити за межі `start()` або `stop()` -- перехоплюйте та логуйте.

### Логування

- Використовуйте `logging.getLogger(__name__)` для логерів конкретного модуля.
- Логуйте на рівні `INFO` для подій життєвого циклу (запущено, зупинено).
- Логуйте на рівні `WARNING` для відновлюваних проблем.
- Логуйте на рівні `ERROR` для збоїв, що потребують уваги.

### EventBus

- Підписуйтесь на максимально конкретні типи подій. Підписка на широкі шаблони збільшує накладні витрати обробки.
- Тримайте обробники подій швидкими. Якщо обробка займає більше кількох мілісекунд, передайте роботу фоновій задачі.
- Використовуйте змістовні назви типів подій за конвенцією `domain.action` (наприклад, `device.state_changed`, `automation.triggered`).

### База даних

- Використовуйте впроваджену `session_factory` для всіх операцій з базою даних. Не створюйте власний engine.
- Надавайте перевагу допоміжним методам (`fetch_devices`, `get_device_state`, `patch_device_state`, `register_device`) перед сирим SQL, коли це можливо.
- Тримайте транзакції короткими для уникнення проблем із блокуванням.

### REST API

- Усі маршрути автоматично отримують префікс `/api/ui/modules/{name}/` -- не повторюйте ім'я модуля у шляхах маршрутів.
- Використовуйте Pydantic-моделі для валідації запитів та відповідей.
- Повертайте узгоджені JSON-структури між ендпоінтами.

### Іменування

- Ім'я каталогу модуля: `snake_case` (наприклад, `battery_monitor`).
- Поле `name` модуля у маніфесті та класі: `kebab-case` (наприклад, `battery-monitor`).
- Тримайте їх узгодженими -- завантажувач автоматично перетворює між ними.

---

## Усунення несправностей

### Модуль не завантажується

- Перевірте, що `manifest.json` існує і `type` дорівнює `"SYSTEM"`.
- Перевірте, що `__init__.py` експортує `module_class`.
- Перегляньте логи ядра на наявність помилок імпорту -- синтаксична помилка у `module.py` запобіжить завантаженню.

### Події не надходять

- Переконайтеся, що ви підписуєтесь на правильний рядок типу події (точний збіг, з урахуванням регістру).
- Переконайтеся, що `subscribe()` викликається у `start()`, а не у `__init__()` (шина недоступна до виклику `setup()`).
- Перевірте, що публікуючий модуль дійсно генерує подію.

### API-маршрути повертають 404

- Перевірте, що `get_router()` повертає не-None `APIRouter`.
- Перевірте, що URL містить повний префікс: `/api/ui/modules/{name}/your-route`.
- Перевірте, що модуль успішно завантажився (шукайте подію `module.started` у логах).

### Помилки бази даних

- Переконайтеся, що ви використовуєте `await` з усіма допоміжними методами бази даних -- вони асинхронні.
- Якщо потрібен прямий доступ до сесії, використовуйте `async with self._session_factory() as session:` та правильно виконуйте commit/rollback.

### Модуль падає при запуску

- Обгорніть важку ініціалізацію у блоки try/except всередині `start()`.
- Якщо модуль залежить від іншого модуля, слухайте його подію `module.started` перед продовженням, замість того щоб припускати, що він вже працює.
