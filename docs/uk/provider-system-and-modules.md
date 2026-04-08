# Система provider'ів, авто-маршрутизація та модуль lights-switches

> Архітектурний рефакторинг SelenaCore після Gree. Перетворює device-control
> на runtime-плагований provider-system, додає SYSTEM-модуль `lights-switches`,
> об'єднує налаштування energy-monitor і виправляє кілька UX-помилок віджетів
> (компактні рядки, Swift-перемикачі, надійність кліків).

## 1. Цілі

Після поставки фічі Gree / Climate (коміти `7b52286..75e5782`) модуль
device-control пре-вшивав усі бібліотеки розумних пристроїв в образ
контейнера. Наступний рефакторинг (коміти `9eb2b61..6ec879e`) одночасно
розв'язує шість больових точок:

1. **Кожен клієнт встановлює лише потрібні йому provider'и.** Tuya,
   Gree, Hue, ESPHome, Zigbee, MQTT — opt-in через вкладку Providers
   у налаштуваннях device-control. Без rebuild, без рестарту контейнера.
2. **Додавання пристрою — один клік.** Імпортований пристрій авто-
   маршрутизується у потрібний модуль (climate / lights-switches) за
   `entity_type` І авто-реєструється як джерело energy-monitor. Без
   ручного зв'язування.
3. **Стійкість до перезапусків.** Втрата живлення, hard restart,
   перестворення контейнера — стан provider'ів зберігається у БД
   реєстру SQLite.
4. **Віджет device-control зникає** з дашборду. Список пристроїв
   переїхав у energy-monitor (read-only статистика з фільтрами/
   сортуванням/клік-у-модалку).
5. **Новий SYSTEM-модуль `lights-switches`** дзеркалить climate для
   `entity_type ∈ {light, switch, outlet}` з повним керуванням (on/off
   + яскравість + колірна температура + RGB).
6. **Сторінка налаштувань energy-monitor** об'єднує 3 окремі секції
   (Споживання, Порогові, Огляд) в одну фільтровану сортовану таблицю.

## 2. Provider system

### 2.1 Архітектура

```
                        ┌──────────────────────────────────┐
                        │  device-control (SystemModule)   │
                        │                                  │
   user тисне Install   │  ProviderLoader                  │
   на "Philips Hue"  ─► │  ├─ install(provider_id)         │
                        │  │   └─ subprocess pip install   │
                        │  │   └─ importlib.import_module  │
                        │  │   └─ INSERT row у БД          │
                        │  │   └─ DRIVERS[id] = cls        │  ◄─ hot-load,
                        │  │                                │     без рестарту
                        │  ├─ load_enabled() (на старті)   │
                        │  │   └─ обхід рядків БД          │
                        │  │   └─ importlib.import_module  │
                        │  │   └─ ImportError → last_error │  ◄─ graceful
                        │  │                                │
                        │  └─ uninstall(provider_id)       │
                        │      └─ enabled=False або        │
                        │          pip uninstall + DELETE  │
                        │                                  │
                        │  drivers/registry.py             │
                        │  ├─ DRIVERS dict (мутується      │
                        │  │  loader'ом, ніколи не         │
                        │  │  замінюється цілком)          │
                        │  └─ get_driver() lookup          │
                        └──────────────────────────────────┘
                                      │
                            ┌─────────┴─────────┐
                            ▼                   ▼
                  /var/lib/selena/      /usr/local/lib/python3.11/
                  registry.db            site-packages/
                  (DriverProvider        (greeclimate, tinytuya,
                   table, persistent)    phue, aioesphomeapi…)
```

І БД реєстру, і pip site-packages знаходяться **поза** glob'ом
integrity-агента (`/opt/selena-core/core/**/*.py`), тож встановлення
нового provider'а ніколи не тригерить порушення цілісності.

### 2.2 Шар зберігання

`core/registry/models.py` додає клас `DriverProvider`:

| Колонка         | Тип       | Значення |
| --------------- | --------- | -------- |
| `id`            | str (PK)  | id з каталогу, напр. `gree` |
| `package`       | str?      | назва pip-пакета (null для stub-провайдерів) |
| `version`       | str?      | специфікація версії, напр. `>=2.1` |
| `enabled`       | bool      | якщо false, loader пропускає |
| `auto_detected` | bool      | true для built-ins, засіяних на першому старті |
| `installed_at`  | datetime  | UTC timestamp |
| `last_error`    | text?     | повідомлення ImportError якщо завантаження впало |

Таблиця створюється `Base.metadata.create_all` на першому старті.
Скрипт міграції не потрібен.

### 2.3 Каталог

`system_modules/device_control/providers/catalog.py` — **єдине джерело
правди** про відомі provider'и. Кожен запис — `ProviderSpec` з полями:

```python
{
    "id":             "gree",
    "name":           "Gree / Pular WiFi A/C",
    "description":    "Локальне керування кондиціонерами Gree…",
    "package":        "greeclimate",
    "version":        ">=2.1",
    "driver_module":  "system_modules.device_control.drivers.gree",
    "driver_class":   "GreeDriver",
    "entity_types":   ["air_conditioner"],
    "needs_cloud":    False,
    "builtin":        True,
    "icon":           "❄️",
    "homepage":       "https://github.com/cmroche/greeclimate",
}
```

**Built-in** (поставляються встановленими у `requirements.txt`,
авто-визначаються на першому старті): `tuya_local`, `tuya_cloud`,
`gree`, `mqtt`.

**Opt-in extras** (вимагають явного Install через UI): `philips_hue`,
`esphome`, `zigbee2mqtt`.

Щоб додати новий provider:
1. Створіть клас драйвера у `system_modules/device_control/drivers/`.
2. Додайте `ProviderSpec` у `PROVIDERS` у `catalog.py`.

Все. Вкладка Providers авто-побачить новий запис, loader імпортує
його на вимогу, агент цілісності його ігнорує.

### 2.4 Loader

`system_modules/device_control/providers/loader.py`:

| Метод                  | Призначення |
| ---------------------- | ----------- |
| `bootstrap_builtins()` | На першому старті імпортує модуль драйвера кожного built-in для перевірки доступності, потім INSERT рядка з `auto_detected=True`. |
| `load_enabled()`       | Обхід таблиці БД, `importlib.import_module()` для кожного enabled, заповнення `self.drivers`. ImportError → `last_error` зберігається, провайдер пропускається. |
| `install(id)`          | `subprocess.run(["pip", "install", spec])` у потоці. Після успіху: верифікація імпорту, UPSERT рядка БД, мутація `drivers/registry.DRIVERS` в місці. |
| `uninstall(id, …)`     | Видалення з `drivers/registry.DRIVERS` + DELETE рядка. Опціонально `pip uninstall -y` (вимкнено за замовчуванням). |
| `list_state()`         | Об'єднує каталог з рядками БД для UI вкладки Providers. |

Рядок БД комітиться **лише після** успіху pip. Якщо pip помирає
посередині (втрата живлення), рядок не комітиться — стан half-state
неможливий. Наступна спроба install безпечна.

### 2.5 Контракт hot-reload

Після успішного `install()` loader **мутує `drivers.registry.DRIVERS`
в місці**, а не замінює його. Існуючі watchers (які тримають
посилання на екземпляри драйверів, не на dict DRIVERS) працюють далі
без змін. НОВІ пристрої одразу використовують щойно імпортований клас
драйвера. Перезапуск контейнера не потрібен.

Після `uninstall()` існуючі watchers продовжують з кешованими
екземплярами до наступного reconnect, де graceful-fail з
`DriverError("Provider not installed")` і пристрій показується offline.

### 2.6 Стійкість до перезапусків

| Сценарій збою                          | Результат |
| -------------------------------------- | --------- |
| Перезапуск контейнера                  | `bootstrap_builtins()` no-op для уже засіяних рядків; `load_enabled()` переімпортує кожен enabled драйвер. Пристрої переконнектяться через існуючу логіку watcher. |
| Втрата живлення під час `pip install`  | Жодного committed рядка → наступний старт бачить тільки попередньо встановлені провайдери. Частково розпакований пакет на диску — нешкідливий. |
| `pip uninstall` race з watchers        | Watchers, що використовують драйвер, отримують DriverError на наступному reconnect, пристрої показуються offline. UI показує червоний бейдж. |
| Користувач витирає `/var/lib/selena/`  | БД пере-створюється порожньою → `bootstrap_builtins()` пере-засіює 4 built-ins → список пристроїв втрачається (це задокументований "factory reset" шлях). |
| Пакет provider'а став неімпортовним    | `load_enabled()` пише ImportError у `last_error`, пропускає провайдер, device-control все одно стартує. UI показує червоний бейдж з повідомленням і кнопку Reinstall. |

### 2.7 Сумісність з integrity-агентом

Агент у `agent/integrity_agent.py` слідкує **лише** за
`/opt/selena-core/core/**/*.py` (див. [agent/manifest.py](../../agent/manifest.py)).

Provider system розміщує все ПОЗА цією зоною:
- Каталог + loader: `/opt/selena-core/system_modules/device_control/providers/`
- Класи драйверів: `/opt/selena-core/system_modules/device_control/drivers/`
- pip site-packages: `/usr/local/lib/python3.11/site-packages/`
- Рядок БД: `/var/lib/selena/registry.db`

**Жодних змін у код агента не потрібно.** Встановлення нового
provider'а для нього невидиме.

### 2.8 UI вкладки Providers

`system_modules/device_control/settings.html` додає нову вкладку
**«Provider'и»** поряд з Devices / Tuya Cloud Wizard / Gree-Pular.

REST endpoints:
- `GET /api/ui/modules/device-control/providers` → список зі станом
- `POST /providers/{id}/install` → `{ok, message, restart_needed: false}`
- `POST /providers/{id}/uninstall` → body `{remove_package?: bool}`

Повна локалізація EN/UK через inline `var L = {en, uk}` згідно
CLAUDE.md §3.1.

## 3. Авто-маршрутизація на `device.registered`

### 3.1 Збагачений payload подій

Кожен code-path, що створює Device (manual POST, Tuya import, Gree
import) тепер публікує `device.registered` з ПОВНИМ payload:

```python
{
    "device_id":     "73ccd8c3-...",
    "name":          "Вітальня",
    "entity_type":   "air_conditioner",
    "location":      "living room",
    "protocol":      "gree",
    "capabilities":  ["on", "off", "set_temperature", "set_mode", ...],
}
```

`device.removed` теж збагачений — підписники знають який `entity_type`
зник.

### 3.2 Класифікатор entity_type для Tuya

Раніше Tuya cloud import призначав `entity_type="switch"` **кожному**
імпортованому пристрою. Новий helper `_classify_tuya_entity_type()`
у `routes.py` робить best-effort з `category` + `product_name` + `name`:

| Сигнал Tuya                                          | entity_type |
| ---------------------------------------------------- | ----------- |
| `category="dj"` АБО ключ `light/lamp/bulb/led/лампа/світло` | `light` |
| `category="cz"` АБО `socket/outlet/plug/розетка`     | `outlet` |
| `category="fs"` АБО `fan/вентилятор`                 | `fan` |
| решта                                                | `switch` (fallback) |

Лампи додатково отримують `brightness` / `colour_temp` capabilities,
якщо Tuya status payload містить відповідні DPS коди.

Користувач завжди може перевизначити через `PATCH /devices/{id}`.

### 3.3 Підписники

Три модулі підписані на `device.registered` / `device.removed`:

| Модуль             | Дія на `device.registered` | Дія на `device.removed` |
| ------------------ | -------------------------- | ----------------------- |
| **energy-monitor** | Якщо source для цього device_id ще немає, `add_source(type="device_registry")`. Авто-відстеження кожного нового пристрою. | Знайти source за device_id → `delete_source()`. |
| **climate**        | (DB-запит на наступному `GET /rooms` — без pre-fetch.) | Дроп cache entry якщо `entity_type` air_conditioner / thermostat. |
| **lights-switches**| Те саме — DB-driven на наступному запиті. | Дроп cache entry якщо `entity_type` light/switch/outlet. |

Це єдиний "routing layer" — центрального capability router'а немає.
Кожен consumer-модуль володіє своїм списком фільтрів і сам вирішує
чи релевантна йому подія.

## 4. SYSTEM-модуль lights-switches

`system_modules/lights_switches/` повністю дзеркалить climate, але для
`entity_type ∈ {light, switch, outlet}`.

### 4.1 Файли

| Файл             | Призначення |
| ---------------- | ----------- |
| `__init__.py`    | Експорт `module_class = LightsSwitchesModule` |
| `manifest.json`  | type=SYSTEM, без порту, `entities: ["light","switch","outlet"]`, widget 2x2 |
| `icon.svg`       | Гліф лампи |
| `module.py`      | `LightsSwitchesModule(SystemModule)` — кешує state + ватти, підписки на події, cross-module виклик device-control через `get_sandbox().get_in_process_module(...)` |
| `routes.py`      | `GET /devices`, `GET /rooms`, `GET /device/{id}`, `POST /device/{id}/command`. Валідує `ALLOWED_STATE_KEYS = {on, brightness, colour_temp, rgb_color}`. |
| `widget.html`    | Dual-mode (компактні рядки на дашборді, full-control картки в модалці). |
| `settings.html`  | Read-only діагностична таблиця. |

### 4.2 Контроли модалки

Модалка показує capability-aware контроли:

- **Power**: Swift-style sliding toggle (замість старої emoji-кнопки)
- **Brightness slider**: 0–100, debounced 250ms, лише якщо `brightness` у capabilities
- **Colour temperature slider**: 0–100
- **RGB picker**: HTML5 `<input type="color">`

Слайдери оновлюють label наживо; команда летить після 250ms idle, тож
драгом неможливо заспамити пристрій 100 командами.

### 4.3 Голосові інтенти

Lights-switches **не володіє жодним голосовим інтентом**. Існуючі
`device.on` / `device.off` у device-control вже працюють для ламп
і вимикачів через entity_filter. Якщо колись додаватимемо керування
яскравістю голосом — це піде у device-control як `device.set_brightness`.

## 5. Рефакторинг energy-monitor

### 5.1 Налаштування — три секції в одну таблицю

Старий layout мав три окремі секції (Огляд / Споживання / Порогові).
Новий layout збиває їх в одну:

1. **KPI strip** (збережено): Status / Tracked / Total Power / Today kWh
2. **Filter bar**: пошук + Type dropdown + Room dropdown + Status dropdown
3. **Сортована таблиця**: Назва | Кімната | Тип | Стан | Потужність (W) | Сьогодні (kWh)
   - Клік на TH → сортування (toggle asc/desc)
   - Фільтри миттєво оновлюють таблицю
   - Room dropdown авто-заповнюється з даних
4. **Компактна панель Thresholds** (збережено)

### 5.2 Новий endpoint

`GET /api/ui/modules/energy-monitor/energy/devices/full` — об'єднує
Device registry з current power + today kWh + source state в один
ready-to-render список.

### 5.3 Віджет — клікабельний, відкриває модалку зі списком пристроїв

1×1 плитка дашборду тепер **click-through**:

- `pointer-events: none` на дочірніх + `cursor: pointer` на body
- `pointerdown` + `click` triggers (захист від cross-iframe focus)
- Клік → `postMessage({type:'openWidgetModal', module:'energy-monitor', width:760, height:580})`

Modal-режим (`?modal=1`) рендерить інший layout: 4 KPI картки + filter
bar + та сама сортована таблиця. `Esc` або × → `closeWidgetModal`.
Це стає де-факто "усі мої пристрої" view, замінюючи старий
device-control widget.

## 6. Еволюція UX віджетів

Після live-тестування вийшло кілька раундів UX-фіксів.

### 6.1 Компактні рядки дашборду

Climate і lights-switches віджети переробились під вузькі плитки:

| Віджет          | Вміст компактного рядка |
| --------------- | ----------------------- |
| **climate**     | `● [name] [target_temp]°` |
| **lights-switches** | `● [name]` (тільки крапка статусу + назва) |

Mode/fan/swing/current temp/brightness/colour — все в модалці. Рядок
суто інформаційний ("чи увімкнено? на чому стоїть?"). Керування — або
голос, або клік→модалка.

### 6.2 Прибрано заголовки кімнат у компактному режимі

Раніше кожна група рядків мала заголовок `[НАЗВА КІМНАТИ]`. На вузьких
плитках це з'їдало вертикальне місце і назви пристроїв не вміщались.
Compact-режим тепер ховає заголовки кімнат повністю
(`.room-title { display: none }`); modal-режим залишає їх (там багато
горизонтального місця).

### 6.3 Виправлення надійності кліків

Три тонкі баги, виправлені в обох віджетах:

**Баг 1: Перший клік на рядок не спрацьовував (треба було тиснути
прямо на текст).** CSS правило `.row > * { pointer-events: none }`
впливало лише на ПРЯМИХ дітей, але climate row мав
`<span class="row-temp"><span class="now">22°</span></span>` —
`.now` це онук, який досі ловив pointer events і "з'їдав" їх.
Виправлено: `.row * { pointer-events: none }` (всі нащадки).

**Баг 2: Усередині модалки перший клік на chip/кнопку не спрацьовував.**
Звичайний `onclick` вимагає focus iframe. Перший клік давав focus,
другий — тригер. Виправлено: helper `tap()`, який реєструє
`pointerdown` АБО `click` з 250ms guard, плюс `window.focus()` у
`bindFullCards()` щоб iframe схопив focus при першому рендері.

**Баг 3: Назву обрізало до "2 літер з трикрапкою" на вузьких плитках.**
Flex item `.row-name` мав `flex: 1` + `white-space: nowrap` +
`text-overflow: ellipsis`, але **без `min-width: 0`**. Без цього flex
children за замовчуванням мають `min-width: auto`, тобто не можуть
стиснутись нижче intrinsic content width. Виправлено: `min-width: 0`
на `.row` І `.row-name`. Тепер назва стискається граційно і
ellipsis активується лише коли реально немає місця.

### 6.4 Swift-style power toggle

Стара `<button>⏻</button>` показувала зламаний/відсутній гліф на
системах без потрібного emoji-шрифту. Замінено на CSS sliding toggle:

```html
<label class="toggle">
  <input type="checkbox" checked>
  <span class="slider"></span>
</label>
```

Чиста CSS-анімація, без JS, без картинок, без emoji-шрифтів. Climate
використовує зелений (`--gr`); lights — амбер (`--am`) для ламп і
зелений для вимикачів/розеток.

### 6.5 Протокол modal-sizing

`Dashboard.tsx` приймає `modal_resize { width, height }` postMessage
від будь-якого віджета. При відкритті модалки віджет може передати
`openWidgetModal { module, width, height }` payload щоб виставити
початковий розмір (уникає мерехтіння "відкрилось велике → resize до
малого"). Панель плавно transition'ить (`transition: width .18s
ease-out, height .18s ease-out`).

## 7. Файли

### Створено

| Шлях | Призначення |
| ---- | ----------- |
| `system_modules/device_control/providers/__init__.py` | Експорти пакета |
| `system_modules/device_control/providers/catalog.py`  | Статичний каталог |
| `system_modules/device_control/providers/loader.py`   | ProviderLoader |
| `system_modules/lights_switches/__init__.py`          | Пакет |
| `system_modules/lights_switches/manifest.json`        | Маніфест |
| `system_modules/lights_switches/icon.svg`             | Іконка |
| `system_modules/lights_switches/module.py`            | LightsSwitchesModule |
| `system_modules/lights_switches/routes.py`            | REST router |
| `system_modules/lights_switches/widget.html`          | Compact + modal віджет |
| `system_modules/lights_switches/settings.html`        | Read-only діагностична сторінка |
| `tests/test_provider_system.py`                       | Тести каталогу + класифікатора |
| `docs/provider-system-and-modules.md`                 | Англійська версія цього документу |
| `docs/uk/provider-system-and-modules.md`              | Цей документ |

### Змінено

| Шлях | Зміна |
| ---- | ----- |
| `core/registry/models.py` | Нова таблиця `DriverProvider` |
| `system_modules/device_control/drivers/registry.py` | DRIVERS тепер починається пустим, заповнюється ProviderLoader'ом |
| `system_modules/device_control/module.py` | Ініціалізація ProviderLoader у `start()` |
| `system_modules/device_control/routes.py` | Нові `/providers/*` endpoints + Tuya entity classifier + збагачені payload подій |
| `system_modules/device_control/settings.html` | Нова вкладка «Provider'и» |
| `system_modules/device_control/manifest.json` | Прибрано `ui.widget` блок |
| `system_modules/device_control/widget.html` | ВИДАЛЕНО |
| `system_modules/energy_monitor/module.py` | Підписка на life-cycle події + `_join_devices()` + `/devices/full` |
| `system_modules/energy_monitor/settings.html` | Три секції → одна уніфікована таблиця |
| `system_modules/energy_monitor/widget.html` | Click-through + modal-режим |
| `system_modules/climate/module.py` | Підписка на device.registered/removed |
| `system_modules/climate/widget.html` | Slim рядки, roomLabel(), Swift toggle, click reliability fixes |

## 8. Перевірка

### Функціональна
1. `pytest tests/ -q` → 72 passed.
2. `docker compose restart core` → провайдери завантажились, всі
   built-ins зелені у вкладці Providers.
3. **Auto-routing**: імпорт Pular AC через Gree wizard → climate
   widget показує його за 5 с І energy-monitor sources має новий
   запис.
4. **Tuya light import**: імпорт Tuya лампи → потрапляє у
   lights-switches widget зі слайдером яскравості, НЕ у climate.
5. **Energy unified table**: відкрий energy-monitor settings → всі
   пристрої в одній фільтрованій таблиці.
6. **Energy widget click**: дашборд energy → fullscreen модалка з тією
   ж таблицею → закриття Esc.
7. **Climate compact row**: лише `● [name] [target temp]°` без
   заголовків кімнат, повна назва видима.
8. **Climate modal**: chip/button клік на ПЕРШОМУ тапі. Power toggle
   плавно ковзає on/off.
9. **Lights compact row**: лише `● [name]`, повна ширина.
10. **Lights modal**: brightness/colour temp/RGB + Swift toggle.

### Стійкість до перезапусків
11. `docker compose down && up -d` → провайдери і пристрої переконнектились.
12. Симуляція втрати живлення мід-`pip install` → restart → жодного
    half-row у БД.

### Integrity agent
13. Після встановлення нового provider'а → 60 с → жодного
    `core.integrity_violation` у `docker logs selena-agent`.

## 9. Критичні файли

- [system_modules/device_control/providers/catalog.py](../../system_modules/device_control/providers/catalog.py) — додавай новий provider тут
- [system_modules/device_control/providers/loader.py](../../system_modules/device_control/providers/loader.py) — install/load/uninstall
- [system_modules/device_control/drivers/registry.py](../../system_modules/device_control/drivers/registry.py) — runtime DRIVERS dict
- [system_modules/lights_switches/module.py](../../system_modules/lights_switches/module.py) — контролер ламп/вимикачів/розеток
- [system_modules/energy_monitor/module.py](../../system_modules/energy_monitor/module.py) — auto-source + `_join_devices()`
- [system_modules/climate/widget.html](../../system_modules/climate/widget.html) — compact + modal climate віджет
- [core/registry/models.py](../../core/registry/models.py) — `DriverProvider` ORM
- [agent/manifest.py](../../agent/manifest.py) — glob слідкування integrity-агента (НЕ розширюй)

## 10. Відомі обмеження

- **pip install може бути повільним на Jetson** для пакетів без
  arm64 wheels. Install у фоновому потоці; UI показує спіннер.
  Дефолтний timeout 5 хвилин.
- **Класифікація entity_type Tuya має false positives** (Tuya розетку,
  яку користувач назвав "Кухонне світло", буде помічено як light).
  Виправлення через `PATCH /devices/{id}`.
- **`importlib.reload()` після install** не завжди оновлює class
  references у вже-запущених watchers — тільки НОВІ пристрої
  використовують щойно імпортований драйвер.
- **Lights-switches v1 обробляє лише Tuya / generic пристрої.** Native
  підтримка Hue / ESPHome залежить від встановлення цих провайдерів
  через вкладку Providers; класи драйверів самі по собі — заглушки,
  які чекають на реалізацію коли пакети потраплять до каталогу.
