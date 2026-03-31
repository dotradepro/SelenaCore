# Посібник з розробки модулів

Цей посібник охоплює все, що потрібно для створення, тестування та розповсюдження користувацьких модулів для SelenaCore.

## Огляд

Користувацькі модулі розширюють SelenaCore додатковою функціональністю. Кожен модуль працює у власному Docker-контейнері та взаємодіє з ядром через WebSocket Module Bus. Така ізоляція гарантує, що несправний модуль не може зламати ядро або заважати іншим модулям.

## Типи модулів

| Тип | Призначення |
|------|---------|
| `UI` | Модулі з візуальним віджетом на панелі та/або сторінкою налаштувань |
| `INTEGRATION` | Інтеграції з зовнішніми сервісами (хмарні API, зовнішні платформи) |
| `DRIVER` | Драйвери апаратних пристроїв (Zigbee-донгли, послідовна периферія) |
| `AUTOMATION` | Механізми правил, планувальники, контролери сцен |
| `IMPORT_SOURCE` | Імпортери даних (CSV, синхронізація баз даних, інструменти міграції) |

> **Примітка:** Тип `SYSTEM` зарезервований для внутрішніх модулів ядра. Користувацькі модулі повинні використовувати один з п'яти типів, перелічених вище.

## Структура модуля

Мінімальний модуль потребує лише `manifest.json` та `main.py`. Повна структура каталогів:

```
my-module/
  manifest.json        # Метадані та можливості модуля (обов'язково)
  main.py              # Точка входу (обов'язково)
  locales/
    en.json            # Англійські переклади
    uk.json            # Українські переклади
  widget.html          # UI-віджет для відображення на панелі
  settings.html        # Сторінка налаштувань модуля
  icon.svg             # Іконка модуля (формат SVG)
  tests/               # Модульні та інтеграційні тести
```

## Довідка по manifest.json

Маніфест оголошує ідентичність вашого модуля, його можливості, дозволи та обмеження ресурсів. Ядро читає цей файл під час встановлення та використовує його для контролю доступу під час виконання.

### Повний приклад

```json
{
    "name": "weather-module",
    "version": "1.0.0",
    "description": "Current weather and forecast via Open-Meteo",
    "type": "UI",
    "ui_profile": "FULL",
    "api_version": "1.0",
    "runtime_mode": "always_on",
    "permissions": ["devices.read", "events.publish"],
    "intents": [
        {
            "patterns": {
                "uk": ["погода", "прогноз", "температур"],
                "en": ["weather", "forecast", "temperatur"]
            },
            "priority": 50,
            "description": "Answer weather questions"
        }
    ],
    "publishes": ["weather.module_started"],
    "ui": {
        "icon": "icon.svg",
        "widget": {"file": "widget.html", "size": "2x2"},
        "settings": "settings.html"
    },
    "resources": {"memory_mb": 128, "cpu": 0.25}
}
```

### Опис полів

| Поле | Тип | Опис |
|-------|------|-------------|
| `name` | string | Малі літери, цифри та дефіси, 2-64 символи. Повинно збігатися з атрибутом класу `name` у вашому Python-модулі. |
| `version` | string | Семантична версія `X.Y.Z`. Повинна збігатися з атрибутом класу `version`. |
| `type` | string | Один із: `UI`, `INTEGRATION`, `DRIVER`, `AUTOMATION`, `IMPORT_SOURCE`. |
| `ui_profile` | string | `HEADLESS` (без UI), `SETTINGS_ONLY`, `ICON_SETTINGS` або `FULL` (віджет + налаштування). |
| `api_version` | string | Наразі `"1.0"`. |
| `runtime_mode` | string | `always_on` (працює постійно), `on_demand` (запускається за потребою), `scheduled` (працює за таймером). |
| `permissions` | array | Можливості, які потребує модуль. Див. [Дозволи](#дозволи) нижче. |
| `intents` | array | Визначення голосових намірів. Див. [Наміри](#наміри) нижче. |
| `publishes` | array | Типи подій, які модуль може генерувати. Події, не перелічені тут, відхиляються шиною. |
| `ui` | object | Посилання на UI-ресурси: `icon` (шлях до SVG), `widget` (HTML-файл + розмір сітки), `settings` (HTML-файл). |
| `resources` | object | Обмеження Docker-контейнера: `memory_mb` (ціле число) та `cpu` (дробове, де 1.0 = одне повне ядро). |

### Дозволи

Дозволи контролюють, до чого модуль може звертатися через шину. Запитуйте лише те, що вам потрібно.

| Дозвіл | Надає |
|------------|--------|
| `devices.read` | Читання списку пристроїв та їхнього стану |
| `devices.write` | Зміна стану пристроїв |
| `events.subscribe` | Прослуховування подій EventBus |
| `events.publish` | Генерування подій (обмежено типами у `publishes`) |

### Наміри

Визначення намірів дозволяють вашому модулю відповідати на голосові команди. Кожен намір включає:

- `patterns`: словник з ключами за кодом мови (`en`, `uk` тощо), де кожне значення -- масив підрядків регулярних виразів, що активують цей намір.
- `priority`: ціле число від 0 до 99. Менші значення означають вищий пріоритет. **Користувацькі модулі повинні використовувати 50-99** (0-49 зарезервовано для модулів ядра).
- `description`: зрозумілий людині опис того, що обробляє цей намір.

---

## Довідка по SDK

### Базовий клас SmartHomeModule

Усі модулі наслідуються від `SmartHomeModule`. Імпортуйте його разом з функціями-декораторами:

```python
from sdk.base_module import SmartHomeModule, intent, on_event, scheduled
```

Ваш підклас повинен оголосити два атрибути класу, що відповідають маніфесту:

```python
class MyModule(SmartHomeModule):
    name = "my-module"       # Повинно збігатися з "name" у manifest.json
    version = "1.0.0"        # Повинна збігатися з "version" у manifest.json
```

---

## Декоратори

### @intent(pattern, order=50)

Реєструє метод як обробник голосового наміру.

| Параметр | Тип | Опис |
|-----------|------|-------------|
| `pattern` | str | Шаблон регулярного виразу (без урахування регістру) для зіставлення з мовленням користувача. |
| `order` | int | Пріоритет 0-99. Менші значення спрацьовують першими. Користувацькі модулі повинні використовувати 50-99. |

Декорований метод отримує сирий текст та словник контексту. Поверніть словник результату або сигналізуйте, що цей обробник не може обробити запит:

```python
@intent(r"погода|weather|forecast", order=50)
async def handle_weather(self, text: str, context: dict) -> dict:
    lang = context.get("_lang", "en")

    # Якщо цей обробник не може обробити запит, передати наступному:
    if "weekly" in text:
        return {"handled": False}

    # Інакше повернути відповідь:
    return {
        "tts_text": self.t("current_weather", lang=lang, city="Kyiv", temp=12),
        "data": {"temperature": 12, "condition": "cloudy"},
    }
```

### @on_event(event_type)

Підписка на події EventBus. Підтримує шаблони з підстановкою через `*`.

| Параметр | Тип | Опис |
|-----------|------|-------------|
| `event_type` | str | Рядок типу події. Використовуйте `*` для підстановки (наприклад, `device.*`). |

```python
@on_event("device.state_changed")
async def on_device_changed(self, data: dict) -> None:
    device_id = data.get("device_id")
    new_state = data.get("state")
    self._log.info("Device %s changed to %s", device_id, new_state)

@on_event("device.*")
async def on_any_device_event(self, data: dict) -> None:
    self._log.debug("Device event: %s", data)
```

### @scheduled(cron)

Запуск методу за розкладом.

| Формат | Приклад | Опис |
|--------|---------|-------------|
| Простий інтервал | `"every:30s"` | Кожні 30 секунд |
| Простий інтервал | `"every:5m"` | Кожні 5 хвилин |
| Простий інтервал | `"every:1h"` | Кожну годину |
| Повний cron | `"*/5 * * * *"` | Кожні 5 хвилин (потребує `apscheduler`) |

```python
@scheduled("every:10m")
async def refresh_cache(self) -> None:
    self._log.debug("Refreshing cache")
    # ... оновлення кешованих даних ...
```

---

## Методи життєвого циклу

Перевизначте ці методи для підключення до життєвого циклу модуля. Усі є необов'язковими.

```python
async def on_start(self) -> None:
    """Викликається один раз перед встановленням з'єднання з шиною.
    Використовуйте для ініціалізації ресурсів, завантаження конфігурації, налаштування стану."""

async def on_stop(self) -> None:
    """Викликається під час коректного завершення роботи.
    Використовуйте для очищення ресурсів, закриття з'єднань, очищення буферів."""

async def on_shutdown(self) -> None:
    """Викликається, коли ядро надсилає повідомлення про завершення.
    Використовуйте лише для збереження стану в останню мить. Тримайте швидким."""
```

---

## Вбудовані методи

### publish_event

Генерування події через шину. Тип події повинен бути зазначений у масиві `publishes` маніфесту.

```python
await self.publish_event("weather.module_started", {"status": "ready"})
await self.publish_event("weather.data_updated", {
    "city": "Kyiv",
    "temperature": 12,
    "condition": "cloudy",
})
```

### api_request

Виклик REST API ядра через проксі шини. Запити підлягають контролю доступу на основі дозволів маніфесту.

```python
result = await self.api_request(method: str, path: str, body: dict | None = None) -> dict
```

Приклади:

```python
# Список усіх пристроїв
devices = await self.api_request("GET", "/devices")

# Отримання конкретного пристрою
device = await self.api_request("GET", f"/devices/{device_id}")

# Оновлення стану пристрою
await self.api_request("PATCH", f"/devices/{device_id}/state", {
    "state": {"power": True}
})

# Публікація події (альтернатива publish_event)
await self.api_request("POST", "/events/publish", {
    "type": "my.custom_event",
    "source": self.name,
    "payload": {"key": "value"},
})
```

### update_capabilities

Гаряче перезавантаження можливостей модуля (повторне оголошення на шині) без перепідключення. Корисно після динамічних змін конфігурації.

```python
await self.update_capabilities()
```

### t (переклад)

Перекладає ключ за допомогою файлів локалізації модуля. Порядок пошуку: запитана мова, потім англійська, потім сирий рядок ключа.

```python
text = self.t(key: str, lang: str | None = None, **kwargs) -> str
```

```python
msg = self.t("current_weather", lang="uk", city="Kyiv", temp=12)
err = self.t("fetch_error", lang="en")
```

### Логування

Кожен модуль має вбудований логер у `self._log`:

```python
self._log.debug("Detailed trace info")
self._log.info("Normal operational message")
self._log.warning("Something unexpected but recoverable")
self._log.error("Something failed: %s", error_message)
```

---

## Змінні оточення

Ядро додає ці змінні оточення до Docker-контейнера модуля при запуску:

| Змінна | Опис | За замовчуванням |
|----------|-------------|---------|
| `SELENA_BUS_URL` | WebSocket-адреса шини | `ws://localhost:7070/api/v1/bus` |
| `MODULE_TOKEN` | Токен автентифікації для підключення до шини | (генерується ядром) |
| `MODULE_DIR` | Абсолютний шлях до робочого каталогу модуля | (встановлюється ядром) |
| `PYTHONPATH` | Включає кореневий каталог проєкту та каталог модуля | (встановлюється ядром) |

---

## Поведінка з'єднання

SDK автоматично керує підключенням до шини:

- **Автоматичне перепідключення** з експоненційною затримкою: починається з 1 секунди, максимум 60 секунд, з 30% джитером для уникнення ефекту "грому стада".
- **Фатальні причини відключення**, що зупиняють спроби перепідключення: `invalid_token`, `permission_denied`. Це вказує на проблеми конфігурації, що потребують ручного втручання.
- **Черга вихідних повідомлень**: до 500 повідомлень буферизуються під час відсутності з'єднання. Черга автоматично очищується при відновленні з'єднання. Повідомлення понад 500 відкидаються (найстаріші першими).

---

## Інтернаціоналізація (i18n)

### Налаштування файлів локалізації

Створіть JSON-файли у каталозі `locales/`, по одному на кожну підтримувану мову:

**locales/en.json**
```json
{
    "current_weather": "{emoji} {city}: {sign}{temp}{unit}, {condition}. Feels like {fl_sign}{feels_like}{unit}. Humidity {humidity}%, wind {wind} m/s",
    "fetch_error": "Could not fetch weather data",
    "module_ready": "Weather module is ready"
}
```

**locales/uk.json**
```json
{
    "current_weather": "{emoji} {city}: {sign}{temp}{unit}, {condition}. Відчувається як {fl_sign}{feels_like}{unit}. Вологість {humidity}%, вітер {wind} м/с",
    "fetch_error": "Не вдалося отримати дані про погоду",
    "module_ready": "Модуль погоди готовий"
}
```

### Використання перекладів

```python
# З іменованими параметрами
msg = self.t("current_weather", lang="uk", city="Kyiv", temp=12,
             sign="+", unit="C", condition="хмарно",
             emoji="cloudy", fl_sign="+", feels_like=9,
             humidity=78, wind=5)

# Простий ключ без параметрів
err = self.t("fetch_error", lang="en")
```

**Порядок пошуку:** запитана мова -> `"en"` -> сирий рядок ключа. Якщо ключ відсутній у всіх файлах локалізації, повертається саме ім'я ключа.

### Правила

- Завжди додавайте переклади до **обох** файлів `en.json` та `uk.json`.
- Повідомлення логера НЕ перекладаються (вони залишаються англійською для зручності налагодження).
- Формат ключів: `section.key` або плоскі ключі (наприклад, `current_weather`, `fetch_error`).

---

## Повний приклад

Ось повністю робочий модуль погоди, що демонструє всі основні можливості SDK:

```python
"""Weather module for SelenaCore."""
import asyncio
from sdk.base_module import SmartHomeModule, intent, on_event, scheduled


class WeatherModule(SmartHomeModule):
    name = "weather-module"
    version = "1.0.0"

    async def on_start(self) -> None:
        """Initialize the module and announce readiness."""
        self._log.info("Weather module started")
        await self.publish_event("weather.module_started", {"status": "ready"})

    @intent(r"погода|прогноз|weather|forecast|temperatur")
    async def handle_weather(self, text: str, context: dict) -> dict:
        """Handle weather-related voice commands."""
        lang = context.get("_lang", "en")

        # Fetch weather data (simplified for this example)
        temperature = 12
        condition = "cloudy"

        return {
            "tts_text": self.t("current_weather", lang=lang,
                               city="Kyiv", temp=temperature,
                               sign="+", unit="C",
                               condition=condition,
                               emoji="cloudy",
                               fl_sign="+", feels_like=9,
                               humidity=78, wind=5),
            "data": {
                "temperature": temperature,
                "condition": condition,
            },
        }

    @on_event("device.state_changed")
    async def on_device_changed(self, data: dict) -> None:
        """React to device state changes."""
        self._log.info("Device changed: %s", data.get("device_id"))

    @scheduled("every:10m")
    async def refresh_cache(self) -> None:
        """Periodically refresh cached weather data."""
        self._log.debug("Cache refreshed")

    async def on_stop(self) -> None:
        """Clean up on shutdown."""
        self._log.info("Weather module stopping")


if __name__ == "__main__":
    module = WeatherModule()
    asyncio.run(module.start())
```

---

## Доступ до API з модулів

Модулі отримують доступ до API ядра виключно через проксі шини (не через прямий HTTP). Усі запити підлягають контролю доступу на основі дозволів маніфесту.

```python
# Список усіх пристроїв (потребує дозвіл devices.read)
devices = await self.api_request("GET", "/devices")

# Отримання конкретного пристрою
device = await self.api_request("GET", f"/devices/{device_id}")

# Оновлення стану пристрою (потребує дозвіл devices.write)
await self.api_request("PATCH", f"/devices/{device_id}/state", {
    "state": {"power": True}
})

# Публікація події (потребує дозвіл events.publish)
await self.api_request("POST", "/events/publish", {
    "type": "my.custom_event",
    "source": self.name,
    "payload": {}
})
```

---

## Точка входу

Кожен модуль повинен містити цей блок у кінці `main.py`:

```python
if __name__ == "__main__":
    module = MyModule()
    asyncio.run(module.start())
```

Метод `start()` (успадкований від `SmartHomeModule`) керує повним життєвим циклом: читання змінних оточення, підключення до шини, автентифікація, реєстрація можливостей та вхід у цикл подій.

---

## Тестування

Використовуйте `mock_core.py` для локального тестування модулів без запущеного екземпляра SelenaCore. Він надає фейкову точку доступу шини, що імітує WebSocket-сервер ядра:

```bash
# Термінал 1: Запуск імітації ядра
python mock_core.py

# Термінал 2: Запуск вашого модуля
SELENA_BUS_URL=ws://localhost:7070/api/v1/bus \
MODULE_TOKEN=test-token \
MODULE_DIR=./my-module \
python my-module/main.py
```

Імітація ядра приймає з'єднання, відповідає на API-запити заглушками та логує всі події, які публікує ваш модуль.

---

## Пакування та встановлення

### Створення пакету

Запакуйте каталог вашого модуля у ZIP-файл:

```bash
cd my-module/
zip -r ../my-module-1.0.0.zip manifest.json main.py locales/ widget.html settings.html icon.svg
```

Переконайтеся, що `manifest.json` знаходиться в корені ZIP-архіву, а не вкладений у підкаталог.

### Встановлення модуля

Завантажте ZIP-файл на запущений екземпляр SelenaCore:

```bash
curl -X POST http://localhost:7070/api/v1/modules/install \
  -F "file=@my-module-1.0.0.zip"
```

Ядро виконає наступне:
1. Перевірить маніфест.
2. Розпакує файли модуля.
3. Створить Docker-контейнер зі вказаними обмеженнями ресурсів.
4. Запустить модуль та встановить з'єднання з шиною.

---

## Типові шаблони

### Відповідь на голосову команду та оновлення пристрою

```python
@intent(r"turn on|увімкни", order=60)
async def handle_turn_on(self, text: str, context: dict) -> dict:
    lang = context.get("_lang", "en")
    device_id = self._parse_device(text)

    await self.api_request("PATCH", f"/devices/{device_id}/state", {
        "state": {"power": True}
    })

    return {"tts_text": self.t("device_on", lang=lang, device=device_id)}
```

### Реагування на події та публікація нових

```python
@on_event("sensor.temperature_changed")
async def on_temp_change(self, data: dict) -> None:
    temp = data.get("value", 0)
    if temp > 30:
        await self.publish_event("automation.alert", {
            "message": f"High temperature: {temp}C",
            "severity": "warning",
        })
```

### Періодичне отримання даних з обробкою помилок

```python
@scheduled("every:5m")
async def poll_external_api(self) -> None:
    try:
        result = await self._fetch_data()
        await self.publish_event("integration.data_updated", result)
    except Exception as exc:
        self._log.error("Failed to poll API: %s", exc)
```

---

## Контрольний список

1. Створіть `manifest.json` з унікальним `name`, правильним `type` та мінімальними `permissions`.
2. Створіть `main.py` з класом, що наслідується від `SmartHomeModule`.
3. Встановіть атрибути класу `name` та `version`, що відповідають маніфесту.
4. Реалізуйте `on_start` для ініціалізації.
5. Додайте обробники намірів, слухачі подій та заплановані завдання за потребою.
6. Створіть файли локалізації у `locales/` для всіх підтримуваних мов.
7. Додайте точку входу `if __name__ == "__main__"`.
8. Протестуйте локально за допомогою `mock_core.py`.
9. Запакуйте як ZIP та встановіть через `POST /api/v1/modules/install`.
