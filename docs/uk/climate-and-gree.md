# Керування кліматом та підтримка кондиціонерів Gree / Pular

> Локальне керування Wi-Fi кондиціонерами на протоколі Gree (Pular GWH12AGB-I-R32, Gree, Cooper&Hunter, EWT, родина Ewpe Smart) плюс окремий високорівневий модуль **Climate** з картками кліматичних пристроїв, згрупованими по кімнатах.
>
> Без хмарного акаунта, без залежностей від Home Assistant, без httpx між системними модулями — лише прямі Python-виклики в межах одного процесу та EventBus.

## 1. Огляд

Фіча додає два шари:

| Шар | Призначення | Модуль |
| --- | --- | --- |
| **Драйвер** | Спілкування з кондиціонером по протоколу Gree (UDP/7000, AES-ECB) | `device-control` (новий драйвер `gree`) |
| **UI-модуль** | Відображення всіх кліматичних пристроїв по кімнатах та керування ними | Новий SYSTEM-модуль `climate` |

Шари свідомо роз'єднані:

- **device-control володіє голосовими інтентами та реєстром пристроїв.** Нові інтенти для кондиціонера (`device.set_temperature`, `device.set_mode`, `device.set_fan_speed`) живуть поряд з існуючими `device.on` / `device.off`. Усі вони використовують один резолвер, який звужує вибір через фільтр `entity_type`, тож команда «встанови температуру» не може випадково потрапити на лампу.
- **Climate-модуль — лише презентація.** Він **не** володіє жодним голосовим інтентом, **не** опитує пристрій, **не** говорить по HTTP. Він підписується на `device.state_changed` для свіжості кешу та передає дії користувача в `DeviceControlModule.execute_command()` прямим викликом Python (між-модульний виклик у межах одного процесу `selena-core` дозволений).
- **Енергоспоживання — це задача `energy_monitor`**, не клімату. Драйвер Gree свідомо не реалізує `consume_metering()`.

## 2. Архітектура

```
                            ┌─────────────────────────┐
                            │   selena-core (один     │
                            │   процес Python)        │
                            │                         │
        Голосовий інтент ─► │   device-control        │
        device.set_         │   ├─ _on_voice_intent   │
        temperature/mode    │   ├─ _resolve_device    │  ─┐
                            │   │  (entity_filter)    │   │
                            │   ├─ execute_command    │   │
                            │   └─ _watch_device      │   │
                            │       │                 │   │
                            │       ▼                 │   │
                            │   GreeDriver (gree.py)  │   │
                            │       │ greeclimate     │   │
                            │       ▼ UDP/7000 AES    │   │
                            │   ┌─────────────┐       │   │
                            │   │ Pular AC    │       │   │
                            │   └─────────────┘       │   │
                            │                         │   │
                            │   climate module        │   │
                            │   ├─ widget /rooms      │   │
                            │   ├─ apply_command()    ───┘
                            │   │  (in-process call)  │
                            │   └─ _on_state_event    │ ◄─ device.state_changed
                            │       (кеш)             │   на EventBus
                            └─────────────────────────┘
```

Ключові інваріанти:

1. **Один екземпляр драйвера на пристрій, одна корутина-вотчер на пристрій.** Реконнект з експоненційним бек-офом при `DriverError`.
2. **Драйвер мутує `self.meta` на місці** (Gree вивчає AES-ключ під час `bind()`); `_persist_driver_meta()` записує дифф у БД після кожного `connect()`, тож після рестарту повторний бінд не потрібен.
3. **Між-модульний виклик через `get_sandbox().get_in_process_module("device-control")`** — без HTTP, без httpx, без портів. Climate-модуль кешує посилання на `device-control` ліниво.
4. **Climate-модуль ніколи не володіє голосовими інтентами.** Уся голосова маршрутизація централізована в `device-control._on_voice_intent`.

## 3. Додані та змінені файли

### Додані

| Шлях | Призначення |
| --- | --- |
| [system_modules/device_control/drivers/gree.py](../../system_modules/device_control/drivers/gree.py) | `GreeDriver(DeviceDriver)` — async обгортка над `greeclimate` |
| [system_modules/climate/__init__.py](../../system_modules/climate/__init__.py) | Експортує `module_class = ClimateModule` |
| [system_modules/climate/manifest.json](../../system_modules/climate/manifest.json) | Маніфест SYSTEM-модуля, без порту |
| [system_modules/climate/module.py](../../system_modules/climate/module.py) | `ClimateModule(SystemModule)` |
| [system_modules/climate/routes.py](../../system_modules/climate/routes.py) | `/devices`, `/rooms`, `/device/{id}/command` |
| [system_modules/climate/widget.html](../../system_modules/climate/widget.html) | 2x2 сітка карток A/C по кімнатах |
| [system_modules/climate/settings.html](../../system_modules/climate/settings.html) | Read-only діагностична таблиця |
| [system_modules/climate/icon.svg](../../system_modules/climate/icon.svg) | Іконка модуля |
| [tests/test_gree_driver.py](../../tests/test_gree_driver.py) | 15 unit-тестів для маперів |

### Змінені

| Шлях | Зміна |
| --- | --- |
| [requirements.txt](../../requirements.txt) | `greeclimate>=2.1` |
| [system_modules/device_control/drivers/registry.py](../../system_modules/device_control/drivers/registry.py) | Реєстрація `"gree": GreeDriver`; запис у `list_driver_types()` |
| [system_modules/device_control/routes.py](../../system_modules/device_control/routes.py) | `POST /gree/discover`, `POST /gree/import`; дозвіл `gree` у `add_device` |
| [system_modules/device_control/settings.html](../../system_modules/device_control/settings.html) | Нова вкладка «Gree / Pular» зі Scan + Import; `air_conditioner` як entity_type; повний EN/UK i18n |
| [system_modules/device_control/module.py](../../system_modules/device_control/module.py) | Нові climate-інтенти, `_intent_to_state()`, `_resolve_device(entity_filter=)`, `_persist_driver_meta()`, розширений `_claim_intent_ownership()` |
| [scripts/seed_intents_to_db.py](../../scripts/seed_intents_to_db.py) | Три нові climate-інтенти, власник — `device-control` |

## 4. Драйвер Gree

### 4.1 Схема логічного стану

Драйвер перекладає між логічним dict-ом SelenaCore (зберігається в `Device.state` як JSON) і об'єктом `greeclimate.device.Device`.

| Ключ | Тип | Діапазон | Опис |
| --- | --- | --- | --- |
| `on` | bool | — | Живлення |
| `mode` | str | `auto` / `cool` / `dry` / `fan` / `heat` | Режим роботи |
| `target_temp` | int | 16–30 | Цільова температура °C (з обмеженням) |
| `current_temp` | int | — | Поточна температура (read-only) |
| `fan_speed` | str | `auto` / `low` / `medium_low` / `medium` / `medium_high` / `high` | Швидкість вентилятора |
| `swing_v` | str | `off` / `full` / `fixed_top` / `fixed_middle_top` / `fixed_middle` / `fixed_middle_bottom` / `fixed_bottom` / `swing_bottom` / `swing_middle` / `swing_top` | Вертикальні жалюзі |
| `swing_h` | str | `off` / `full` / `left` / `left_center` / `center` / `right_center` / `right` | Горизонтальні жалюзі |
| `sleep` | bool | — | Нічний режим |
| `turbo` | bool | — | Турбо-режим |
| `light` | bool | — | Підсвітка дисплея |
| `eco` | bool | — | Steady-heat / еко |
| `health` | bool | — | Анти-іон / здоров'я |
| `quiet` | bool | — | Тихий режим |

### 4.2 Схема `device.meta["gree"]`

```json
{
  "gree": {
    "ip": "192.168.1.50",
    "mac": "aa:bb:cc:dd:ee:ff",
    "name": "Кондиціонер у спальні",
    "port": 7000,
    "key": null,
    "brand": "gree",
    "model": "GWH12AGB"
  }
}
```

`key` — `null` до першого успішного `bind()`. Драйвер записує отриманий AES-ключ у це поле, а `DeviceControlModule._persist_driver_meta()` зливає його в БД, щоб після перезавантаження не довелося повторювати рукостискання.

### 4.3 Життєвий цикл

| Метод | Поведінка |
| --- | --- |
| `connect()` | Створює `Device(DeviceInfo(ip, port, mac, name))`, `await bind(key=...)`, зберігає новий `device_key`, `update_state()`, повертає логічний стан. Будь-який виняток обгортається у `DriverError`. |
| `set_state(state)` | Під `asyncio.Lock` перекладає логічні ключі на атрибути greeclimate, викликає `push_state_update()`. Обмежує `target_temp` діапазоном 16–30 °C. Кидає `DriverError` на невідомий mode/fan/swing. |
| `get_state()` | Lock + `update_state()` + `_to_logical()`. |
| `stream_events()` | Gree-пристрої не пушать події. Цикл з `POLL_INTERVAL_SECONDS = 5`, віддає лише коли стан реально змінився (дифф з `_last_state`). Збої мережі піднімають `DriverError`, що тригерить реконнект вотчера. |
| `disconnect()` | Ідемпотентний — занулює `_device` (greeclimate не тримає постійних сокетів). |
| `consume_metering()` | **Не перевизначений** — енергія належить `energy_monitor`. |

### 4.4 Реєстрація драйвера

```python
# system_modules/device_control/drivers/registry.py
DRIVERS = {
    "tuya_local": TuyaLocalDriver,
    "tuya_cloud": TuyaCloudDriver,
    "mqtt": MqttBridgeDriver,
    "gree": GreeDriver,         # ← новий
}
```

```python
# list_driver_types() — для випадаючого списку «Add device»
{
    "id": "gree",
    "name": "Gree / Pular WiFi A/C",
    "needs_cloud": False,
    "fields": ["gree.ip", "gree.mac", "gree.name"],
}
```

## 5. Виявлення і додавання

### 5.1 REST API

| Метод | Шлях | Тіло | Повертає |
| --- | --- | --- | --- |
| `POST` | `/api/ui/modules/device-control/gree/discover` | `{"timeout": 10}` (необов'язково) | `{"devices": [{ip, mac, name, brand, model, version}, ...]}` |
| `POST` | `/api/ui/modules/device-control/gree/import` | `{"devices": [{ip, mac, name, location}, ...]}` | `{"created": [...], "skipped": [...]}` |

`/gree/discover` запускає `greeclimate.Discovery().scan(timeout=10)` (з резервним викликом для 1.x API). Best-effort: повертає порожній список, якщо `greeclimate` не встановлено або скан кинув виняток.

`/gree/import` створює рядки `Device` з:

- `protocol = "gree"`
- `entity_type = "air_conditioner"`
- `capabilities = AC_CAPABILITIES` (`["on","off","set_temperature","set_mode","set_fan_speed","set_swing"]`)
- `enabled = True`
- `meta.gree = {ip, mac, name, port:7000, key:null, brand:"gree"}`

Після вставки рядка викликається `add_device_watcher()`, який виконує перший `connect()` (і узгоджує ключ). Новий ключ зливається в БД через `_persist_driver_meta()` на тому самому шляху.

### 5.2 UI-флоу

`device-control/settings.html` має третю вкладку **«Gree / Pular»** поряд з *Devices* та *Tuya Cloud Wizard*:

1. Натиснути **Сканувати** → `POST /gree/discover` → індикатор протягом 10 секунд.
2. Таблиця результатів показує IP, MAC, виробник/модель. У кожному рядку — чекбокс **Імпорт** (за замовчуванням увімкнений), редаговане поле **Назва** та **Кімната**.
3. Натиснути **Імпортувати вибрані** → `POST /gree/import` → toast підтвердження → перехід на вкладку *Devices* з новими записами.

Усі рядки UI мають повний переклад EN/UK у словнику `var L = {en:{}, uk:{}}`.

### 5.3 Ручне додавання

Існуючий `POST /devices` теж працює — `protocol="gree"`, `entity_type="air_conditioner"`, `meta={"gree": {"ip": ..., "mac": ..., "name": ...}}`. Вотчер виконає bind при першому підключенні.

## 6. Climate-модуль

### 6.1 Маніфест

```json
{
  "name": "climate",
  "type": "SYSTEM",
  "runtime_mode": "always_on",
  "permissions": ["device.read", "device.write", "events.subscribe", "events.publish"],
  "ui": {
    "icon": "icon.svg",
    "widget": {"file": "widget.html", "size": "2x2"},
    "settings": "settings.html"
  }
}
```

Без `port`. SYSTEM-модулі живуть у процесі `selena-core`.

### 6.2 Між-модульний виклик

```python
# system_modules/climate/module.py
from core.module_loader.sandbox import get_sandbox

self._dc = get_sandbox().get_in_process_module("device-control")
await self._dc.execute_command(device_id, state)
```

Посилання кешується ліниво після першого пошуку. Якщо `device-control` ще не завантажено (race на старті), `apply_command()` кидає `RuntimeError`, а маршрут повертає `503`.

### 6.3 REST API

Підключений на `/api/ui/modules/climate/`:

| Метод | Шлях | Повертає |
| --- | --- | --- |
| `GET` | `/health` | `{status, module, cached_devices}` |
| `GET` | `/devices` | Плоский список усіх пристроїв з `entity_type` ∈ `{air_conditioner, thermostat}` |
| `GET` | `/rooms` | Ті самі дані, згруповані за `location` (порожня кімната → бакет `unassigned`) |
| `GET` | `/device/{id}` | Деталі одного пристрою |
| `POST` | `/device/{id}/command` | Тіло `{state: {...}}`. Перевіряє дозволені ключі (`ALLOWED_STATE_KEYS`) і викликає `DeviceControlModule.execute_command()`. |

Дозволені ключі `state`: `on`, `mode`, `target_temp`, `fan_speed`, `swing_v`, `swing_h`, `sleep`, `turbo`, `light`, `eco`, `health`, `quiet`.

### 6.4 Підписка на EventBus

Модуль підписаний на `device.state_changed` і кешує `payload["new_state"]` у `self._latest[device_id]`. `list_climate_devices()` зливає стан з БД та кешований дельта-апдейт, тому віджет читає за O(1) після першого завантаження.

### 6.5 Віджет

`widget.html` — це 2x2 плитка дашборду:

- Пристрої згруповані по кімнатах (location).
- Кожна картка показує: назву пристрою, кнопку живлення, поточну температуру, цільову температуру з кнопками `+`/`−` (обмеження 16–30), чипи режиму (`auto`/`cool`/`dry`/`fan`/`heat`), чипи швидкості вентилятора (`auto`/`low`/`medium`/`high`).
- Опитує `GET /rooms` кожні 10 с + при `window.focus`.
- Реагує на глобальний postMessage `lang_changed` повним перерендером.
- Повна локалізація EN/UK через `var L = {en:{}, uk:{}}`.

### 6.6 Сторінка налаштувань

`settings.html` навмисно мінімалістичний — лише read-only діагностична таблиця з кімнатою, назвою, типом, протоколом, бейджем on/off і сирим JSON стану. **Без** випадаючого списку «джерело клімату»: усі кліматичні пристрої автоматично з'являються за `entity_type`.

## 7. Голосові команди

> Голосові інтенти живуть у **device-control**, не в climate. Це усуває будь-яке перетинання патернів зі світлом/розетками.

### 7.1 Нові інтенти

| Інтент | Параметри | Приклад (EN) | Приклад (UK) |
| --- | --- | --- | --- |
| `device.set_temperature` | `level: int`, `location?: str` | "set temperature to 22 in bedroom" | "встанови температуру на 22 в спальні" |
| `device.set_mode` | `mode: enum(auto,cool,dry,fan,heat)`, `location?: str` | "switch bedroom to cool mode" | "перемкни спальню в режим охолодження" |
| `device.set_fan_speed` | `level: enum(auto,low,medium,high,min,max,...)`, `location?: str` | "set fan to high in bedroom" | "встанови вентилятор на високу в спальні" |

Аліаси, які обробляє парсер: `min/minimum → low`, `max/maximum → high`, `mid/middle → medium`, `cooling → cool`, `heating → heat`.

Патерни вносяться у таблиці `intent_definitions` / `intent_patterns` через [scripts/seed_intents_to_db.py](../../scripts/seed_intents_to_db.py) з `priority=100` (рівень FastMatcher). Запустіть після міграцій схеми:

```bash
docker exec selena-core python scripts/seed_intents_to_db.py
```

### 7.2 Резолюція пристрою

`DeviceControlModule._resolve_device(params, entity_filter=...)` обирає рівно один цільовий пристрій за існуючою 4-рівневою стратегією (entity+location → location → entity → fallback на одне-єдине). Climate-інтенти передають `entity_filter=("air_conditioner","thermostat")` (або `("air_conditioner","fan")` для `device.set_fan_speed`), щоб резолвер звузив набір кандидатів ще до tier-матчингу. Саме це гарантує, що «встанови температуру на 22» не може випадково потрапити на лампу або розетку.

### 7.3 Власність інтентів

`DeviceControlModule._claim_intent_ownership()` оновлює всі рядки в `intent_definitions`, що перелічені в `OWNED_INTENTS = ["device.on", "device.off", "device.set_temperature", "device.set_mode", "device.set_fan_speed"]`, виставляючи `module="device-control"`. Ідемпотентний — виконується при кожному старті модуля.

## 8. Збереження meta (ключ Gree)

Драйвери, які отримують облікові дані під час `connect()` (AES-ключ Gree), мутують `self.meta` на місці. Цикл вотчера викликає `_persist_driver_meta(device_id, drv)` одразу після кожного успішного `connect()`:

```python
async def _persist_driver_meta(self, device_id: str, drv: Any) -> None:
    new_json = json.dumps(drv.meta, sort_keys=True)
    async with self._db_session() as session:
        async with session.begin():
            d = await session.get(Device, device_id)
            current_json = json.dumps(json.loads(d.meta) if d.meta else {}, sort_keys=True)
            if current_json == new_json:
                return       # no-op якщо нічого не змінилося
            d.set_meta(drv.meta)
```

Перевірка дифу запобігає зайвим записам у БД на кожному циклі реконнекту.

## 9. Перевірка

### 9.1 Unit-тести

```bash
pytest tests/test_gree_driver.py -v
```

15 тестів покривають: список можливостей, обмеження температури (`_clamp_temp`), двосторонній round-trip enum-мап, `_to_logical()` на `MagicMock` greeclimate.Device, відмову на невідомий mode, переклад eco/health/quiet/light, ініціалізацію meta.

Тести підставляють `greeclimate` у `sys.modules` ще до імпорту драйвера, тому вони проходять навіть без встановленого пакета (наприклад, у CI).

### 9.2 Регресійні тести

```bash
pytest tests/test_device_watchdog.py tests/test_energy_monitor.py -q
# 47 passed
```

### 9.3 Перевірка на залізі (end-to-end)

1. `docker compose up -d --build` — обов'язковий rebuild, бо змінився `requirements.txt`.
2. `docker exec selena-core python scripts/seed_intents_to_db.py` — завантажити нові інтенти.
3. **Виявлення**: Device Control → Gree / Pular → Сканувати → побачити Pular → Імпортувати.
4. **Вотчер**: `docker compose logs -f core` → очікувати `device.online`, потім `device.state_changed` приблизно кожні 5 с.
5. **Прямий контроль**: тоглнути on/off у віджеті Device Control → AC реагує.
6. **Climate UI**: відкрити віджет Climate → картка з'являється у потрібній кімнаті → +/− температури, режим, вентилятор, живлення — все відображається на AC.
7. **Голос (UK)**: «встанови температуру на 24» → `voice.intent` → device-control резолвить AC → фізична зміна.
8. **Голос (EN)**: «switch bedroom to cool mode» → резолвиться лише на AC у спальні → режим змінюється.
9. **Persistence після рестарту**: `docker compose restart core` → AC переконнектиться без нового binding (`meta.gree.key` зберігся).

## 10. Обмеження та плани на майбутнє

- **Одне джерело клімату на команду** — multi-room голосові команди («встанови всі кондиціонери на 24») потребують broadcast-шляху; поза скоупом v1.
- **Без розкладів / комфорт-профілів** — Climate-модуль лише презентаційний. Розклади належать `automation-engine`.
- **Без перегляду історії у v1** — `GET /history` не реалізовано; сирі дані лежать у `state_history`, можна додати пізніше.
- **Варіанти прошивок Pular** — якщо discovery повертає порожній список, зніміть LAN-трафік `tcpdump -i any udp port 7000` під час handshake додатка Gree+/Ewpe Smart, щоб виявити відмінності діалекту. Ручне додавання через `POST /devices` завжди працює як резерв.
- **Дрейф API `greeclimate`** — драйвер націлений на v2.x. OEM-специфічні атрибути (`steady_heat`, `anion`, `quiet`) можуть відрізнятися на ребрендованих юнітах; перевіряти на залізі і підправляти мапінг `_to_logical` / `_apply_logical` за потреби.
