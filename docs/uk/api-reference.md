# Довідник REST API SelenaCore

**Базова URL-адреса:** `http://localhost:7070/api/v1`

---

## Автентифікація

Більшість точок доступу вимагають Bearer-токен у заголовку `Authorization`:

```
Authorization: Bearer <module_token>
```

Токени зберігаються на диску в `/secure/module_tokens/`. Для розробки встановіть змінну середовища `DEV_MODULE_TOKEN`.

## Обмеження частоти запитів

Усі автентифіковані точки доступу обмежені до **120 запитів за 60 секунд** на клієнта. Це налаштовується через `RateLimitMiddleware`. Перевищення ліміту повертає `429 Too Many Requests`.

## Заголовки запитів

| Заголовок | Опис |
|---|---|
| `Authorization` | `Bearer <token>` (обов'язковий для більшості точок доступу) |
| `X-Request-Id` | Автоматично згенерований UUID для кожного запиту (додається `RequestIdMiddleware`) |

## Swagger UI

Інтерактивна документація API доступна за адресою `/docs`, коли встановлено змінну середовища `DEBUG=true`. Вимкнено у продакшені.

---

## Системні точки доступу

### GET /health

Повертає поточний стан працездатності екземпляра SelenaCore. **Автентифікація не потрібна.**

**Відповідь 200:**

```json
{
    "status": "ok",
    "version": "0.3.142-beta+0644435",
    "mode": "normal",
    "uptime": 3600,
    "integrity": "ok"
}
```

| Поле | Тип | Значення |
|---|---|---|
| `status` | string | `"ok"` |
| `mode` | string | `"normal"` або `"safe_mode"` |
| `uptime` | int | Секунди з моменту запуску |
| `integrity` | string | `"ok"` або `"violation"` |

---

### GET /system/info

Повертає детальну інформацію про систему та апаратне забезпечення. Потребує автентифікації.

**Відповідь 200:**

```json
{
    "initialized": true,
    "wizard_completed": true,
    "version": "0.3.142-beta+0644435",
    "hardware": {
        "model": "raspberrypi",
        "ram_total_mb": 8192,
        "has_hdmi": false,
        "has_camera": false
    },
    "audio": {
        "inputs": [],
        "outputs": []
    },
    "display_mode": "headless"
}
```

| Поле | Тип | Опис |
|---|---|---|
| `initialized` | bool | Чи завершено ядром першу ініціалізацію |
| `wizard_completed` | bool | Чи завершено майстер налаштування |
| `display_mode` | string | `"headless"` або ідентифікатор дисплея |

---

## Точки доступу пристроїв

Усі точки доступу пристроїв потребують автентифікації.

### GET /devices

Отримати список усіх зареєстрованих пристроїв.

**Відповідь 200:**

```json
{
    "devices": [
        {
            "device_id": "uuid-string",
            "name": "Kitchen Light",
            "type": "actuator",
            "protocol": "zigbee",
            "state": {"power": true, "brightness": 80},
            "capabilities": ["turn_on", "turn_off", "set_brightness"],
            "last_seen": 1711900000.0,
            "module_id": "protocol-bridge",
            "meta": {"manufacturer": "IKEA"}
        }
    ]
}
```

---

### POST /devices

Зареєструвати новий пристрій.

**Запит:**

```json
{
    "name": "Kitchen Light",
    "type": "actuator",
    "protocol": "zigbee",
    "capabilities": ["turn_on", "turn_off"],
    "meta": {}
}
```

| Поле | Тип | Обов'язкове | Опис |
|---|---|---|---|
| `name` | string | так | Зрозуміла людині назва пристрою |
| `type` | string | так | Одне з: `sensor`, `actuator`, `controller`, `virtual` |
| `protocol` | string | так | Протокол зв'язку (наприклад, `zigbee`, `mqtt`, `http`) |
| `capabilities` | list[string] | так | Підтримувані дії |
| `meta` | object | ні | Довільні метадані |

**Відповідь 201:** DeviceResponse (та сама схема, що й елементи у `GET /devices`).

Публікує подію `device.registered` на шині подій.

---

### GET /devices/{device_id}

Отримати окремий пристрій за його UUID.

**Відповідь 200:** DeviceResponse

**Відповідь 404:**

```json
{"detail": "Device not found"}
```

---

### PATCH /devices/{device_id}/state

Оновити стан пристрою.

**Запит:**

```json
{
    "state": {"power": true, "brightness": 80}
}
```

Об'єкт `state` -- це словник довільної форми. Його ключі залежать від можливостей пристрою.

**Відповідь 200:** DeviceResponse (з оновленим станом)

Публікує подію `device.state_changed`, що містить `old_state` та `new_state`.

---

### DELETE /devices/{device_id}

Видалити пристрій з реєстру.

**Відповідь 204:** Без тіла.

Публікує подію `device.removed`.

---

## Точки доступу подій

Усі точки доступу подій потребують автентифікації.

### POST /events/publish

Опублікувати власну подію на шині подій.

**Запит:**

```json
{
    "type": "my.custom_event",
    "source": "my-module",
    "payload": {"key": "value"}
}
```

| Поле | Тип | Обов'язкове | Опис |
|---|---|---|---|
| `type` | string | так | Ідентифікатор типу події (простір імен, розділений крапками) |
| `source` | string | так | Модуль або компонент, що згенерував подію |
| `payload` | object | ні | Довільні дані події |

**Відповідь 201:**

```json
{
    "event_id": "uuid",
    "type": "my.custom_event",
    "timestamp": 1711900000.0
}
```

**Відповідь 403:** Повертається, коли модуль намагається опублікувати подію `core.*`. Лише ядро системи може генерувати події у просторі імен `core`.

---

### POST /events/subscribe

Підписатися на події через webhook-зворотний виклик.

> **Застаріло.** Використовуйте [Module Bus WebSocket](#websocket---module-bus) замість цього.

**Запит:**

```json
{
    "event_types": ["device.state_changed"],
    "webhook_url": "http://localhost:8100/webhook"
}
```

**Відповідь 201:**

```json
{
    "subscription_id": "uuid",
    "event_types": ["device.state_changed"],
    "webhook_url": "http://localhost:8100/webhook"
}
```

---

## Точки доступу модулів

Усі точки доступу модулів потребують автентифікації.

### GET /modules

Отримати список усіх встановлених модулів.

**Відповідь 200:**

```json
{
    "modules": [
        {
            "name": "weather-module",
            "version": "1.0.0",
            "type": "UI",
            "status": "RUNNING",
            "runtime_mode": "always_on",
            "port": 0,
            "installed_at": 1711900000.0,
            "ui": {
                "icon": "icon.svg",
                "widget": {"file": "widget.html", "size": "2x2"}
            }
        }
    ]
}
```

| Поле | Тип | Опис |
|---|---|---|
| `type` | string | Тип модуля (наприклад, `UI`, `SYSTEM`, `SERVICE`) |
| `status` | string | `VALIDATING`, `READY`, `RUNNING`, `STOPPED`, `ERROR` |
| `runtime_mode` | string | `always_on` або `on_demand` |
| `port` | int | Призначений порт (0, якщо не застосовується) |
| `ui` | object або null | Конфігурація UI-віджета, якщо модуль його надає |

---

### POST /modules/install

Встановити модуль із ZIP-архіву. Використовує завантаження multipart form.

**Запит:**

```
Content-Type: multipart/form-data
Field: module (file, .zip)
```

**Відповідь 201:**

```json
{
    "name": "my-module",
    "status": "VALIDATING",
    "message": "Module uploaded, validation in progress"
}
```

Встановлення виконується асинхронно. Використовуйте SSE-потік для відстеження прогресу.

---

### GET /modules/{name}/status/stream

Потік Server-Sent Events для відстеження встановлення модуля та змін життєвого циклу.

**Відповідь:** `text/event-stream`

```
data: {"status": "VALIDATING", "message": "Manifest validated, installing..."}
data: {"status": "READY", "message": "Validation passed, starting..."}
data: {"status": "RUNNING", "message": "Module started"}
```

Повідомлення heartbeat надсилається кожні 30 секунд, якщо немає оновлень статусу.

---

### POST /modules/{name}/start

Запустити зупинений модуль.

**Відповідь 200:**

```json
{"name": "my-module", "status": "RUNNING"}
```

---

### POST /modules/{name}/stop

Зупинити працюючий модуль.

**Відповідь 200:**

```json
{"name": "my-module", "status": "STOPPED"}
```

**Відповідь 403:** Повертається при спробі зупинити модуль типу `SYSTEM`. Системні модулі не можуть бути зупинені.

---

### DELETE /modules/{name}

Видалити встановлений модуль та очистити його ресурси.

**Відповідь 204:** Без тіла.

**Відповідь 403:** Повертається при спробі видалити модуль типу `SYSTEM`. Системні модулі не можуть бути видалені.

---

## Точки доступу секретів

Усі точки доступу секретів потребують автентифікації.

### GET /secrets

Отримати список ідентифікаторів збережених секретів. Значення ніколи не повертаються у відкритому вигляді.

### POST /secrets

Зберегти OAuth-токен або інший секрет. Секрети шифруються у стані спокою за допомогою AES-256-GCM.

---

## Точки доступу цілісності

Усі точки доступу цілісності потребують автентифікації.

### GET /integrity/status

Повертає поточний стан агента цілісності, який відстежує підробку файлів та конфігурації.

---

## Точки доступу інтентів (застаріло)

> **Застаріло.** Інтенти тепер керуються через механізм `announce` Module Bus. Ці REST-точки доступу залишаються для зворотної сумісності, але будуть видалені у майбутньому релізі.

### GET /intents

Отримати список інтентів, оголошених через Module Bus.

### POST /intents/register

Зареєструвати нові інтенти. Використовуйте `announce` Module Bus замість цього.

---

## WebSocket - Module Bus

### WS /bus?token=TOKEN

WebSocket-точка доступу для комунікації з Module Bus у реальному часі. Модулі підключаються сюди для оголошення можливостей, підписки на події та обміну повідомленнями з ядром.

Передайте токен модуля як параметр запиту `token`.

Див. [Протокол Module Bus](module-bus-protocol.md) для повного довідника формату повідомлень та рукостискання.

---

## UI-маршрути

**Базова адреса:** `/api/ui`

Ці маршрути призначені лише для локального веб-інтерфейсу. Вони захищені правилами iptables (доступ лише з localhost) та **не** потребують Bearer-токенів.

| Маршрут | Опис |
|---|---|
| `POST /api/ui/setup/*` | Кроки майстра налаштування |
| `GET /api/ui/setup/vosk/catalog` | Каталог моделей Vosk для розпізнавання мовлення |
| Точки доступу голосового рушія | Керування конфігурацією STT/TTS |
| Маршрутизація UI модулів | Обслуговування файлів віджетів та іконок модулів |

### Ендпоінти налаштування аудіо

| Метод | Маршрут | Опис |
|-------|---------|------|
| GET | `/api/ui/setup/audio/devices` | Список виявлених ALSA пристроїв |
| POST | `/api/ui/setup/audio/select` | Зберегти вибір пристроїв `{input, output}` |
| POST | `/api/ui/setup/audio/test/output` | Тест динаміка (лівий/правий канал) |
| POST | `/api/ui/setup/audio/test/input` | Запис 3с з мікрофона, вимірювання піку, відтворення |
| GET | `/api/ui/setup/audio/mic-level` | Рівень мікрофона → `{level: 0.0-1.0}` |
| GET | `/api/ui/setup/audio/levels` | Поточні `{output_volume, input_gain}` |
| POST | `/api/ui/setup/audio/levels` | Встановити `{output_volume?, input_gain?}` |
| GET | `/api/ui/setup/audio/sources` | Список аудіо-джерел → `{sources: [{module, name, volume}]}` |
| POST | `/api/ui/setup/audio/sources/volume` | Гучність джерела `{module, volume}` |

---

## Відповіді з помилками

Усі помилки повертають JSON-тіло з полем `detail`.

**Стандартна помилка:**

```json
{
    "detail": "Error message"
}
```

**Помилка валідації (422 Unprocessable Entity):**

```json
{
    "detail": {"errors": ["error1", "error2"]}
}
```

### Загальні коди стану

| Код | Значення |
|---|---|
| 200 | Успіх |
| 201 | Створено |
| 204 | Без вмісту (успішне видалення) |
| 400 | Некоректний запит |
| 401 | Не авторизовано (відсутній або недійсний токен) |
| 403 | Заборонено (недостатньо дозволів) |
| 404 | Не знайдено |
| 422 | Помилка валідації |
| 429 | Забагато запитів (перевищено ліміт частоти) |
| 500 | Внутрішня помилка сервера |

---

## Довідник типів подій

Події використовують простір імен, розділений крапками. Визначені наступні простори імен:

### core.*
Зарезервовано для ядра системи. Модулі не можуть публікувати ці події.

| Подія | Опис |
|---|---|
| `core.startup` | Ядро запущено |
| `core.shutdown` | Ядро завершує роботу |
| `core.integrity_violation` | Виявлено підробку файлу або конфігурації |
| `core.integrity_restored` | Перевірка цілісності пройшла після попереднього порушення |
| `core.safe_mode_entered` | Система перейшла у безпечний режим |
| `core.safe_mode_exited` | Система вийшла з безпечного режиму |

### device.*

| Подія | Опис |
|---|---|
| `device.state_changed` | Стан пристрою оновлено (містить `old_state` та `new_state`) |
| `device.registered` | Додано новий пристрій |
| `device.removed` | Пристрій видалено |
| `device.offline` | Пристрій перестав відповідати |
| `device.online` | Пристрій знову підключився |
| `device.discovered` | Новий пристрій виявлено в мережі |

### module.*

| Подія | Опис |
|---|---|
| `module.installed` | Модуль встановлено |
| `module.started` | Модуль запущено |
| `module.stopped` | Модуль зупинено |
| `module.error` | Модуль зіткнувся з помилкою |
| `module.removed` | Модуль видалено |

### sync.*

| Подія | Опис |
|---|---|
| `sync.command_received` | Отримано віддалену команду з хмарної синхронізації |
| `sync.command_ack` | Надіслано підтвердження команди |
| `sync.connection_lost` | Втрачено з'єднання з хмарною синхронізацією |
| `sync.connection_restored` | Відновлено з'єднання з хмарною синхронізацією |

### voice.*

| Подія | Опис |
|---|---|
| `voice.wake_word` | Виявлено слово активації |
| `voice.recognized` | Мовлення розпізнано |
| `voice.intent` | Інтент витягнуто з мовлення |
| `voice.response` | Згенеровано голосову відповідь |
| `voice.privacy_on` | Мікрофон вимкнено / режим приватності увімкнено |
| `voice.privacy_off` | Мікрофон увімкнено / режим приватності вимкнено |
