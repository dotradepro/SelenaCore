# Рекрафт панелі керування та шаблонний движок віджетів

> Візуальний та архітектурний рекрафт панелі керування SelenaCore. Замінює одноманітну сітку 5×4 з iframe-віджетів на hero-панель, ярлики сцен, вкладки кімнат та bento-сітку зі змішаними розмірами. Піднімає абстракцію віджета з рівня «відрендери цей HTML в iframe» до рівня «оголоси що показати — ядро відрендерить». Залишає шлях через iframe як запасний для кастомних UI.
>
> **➡ Дивись також:** [widget-development.md](widget-development.md) — поточний посібник з віджетів. Цей документ замінить значну його частину після виходу шаблонного движка. [English version](../dashboard-recraft.md).

---

## 1. Огляд

### 1.1 Чому потрібен рекрафт

Поточна панель керування читається як адмін-панель, а не як поверхня розумного дому. П'ять конкретних проблем:

1. **Відсутність контексту.** Користувач відкриває панель і бачить сітку плиток однакової ваги. Немає привітання, часу, зовнішньої погоди, статусу системи — нічого, що б задавало рамку моменту.
2. **Одноманітна сітка.** Кожен віджет займає клітинку однакового розміру у фіксованій сітці 5×4. Немає візуальної ієрархії: клімат у вітальні виглядає так само, як точка з температурою CPU. Це невірна естетика для поверхні керування домом.
3. **20 віджетів, 20 дизайнів.** Кожен автор модуля пише свій HTML, CSS, fetch-логіку, error-стейти, loading-стейти та хуки тем. Результат — візуальна какофонія навіть коли дані схожі.
4. **Iframe на віджет.** N iframe на Pi — це N контекстів браузера, N HTTP-fetcher'ів, N event-loop'ів. Tooltip, hover, focus та анімації не можна шарити між віджетами. Крос-віджетні взаємодії неможливі.
5. **Невикористаний потенціал.** Framer Motion (12.x), lucide-react, Tailwind 4 з `@theme`-директивою та повна система дизайн-токенів у `index.css` уже встановлені й майже не використовуються. Рекрафт здебільшого про композицію, не про нові залежності.

### 1.2 Цілі

Рекрафт має три цілі за пріоритетом:

- **Зробити так, щоб панель читалась як дім, а не адмін-панель.** Hero, сцени, кімнати, змішані розміри віджетів.
- **Уніфікувати вигляд та поведінку віджетів.** Один chrome, один набір станів (loading / error / stale), одна мова руху. Автори модулів припиняють писати CSS.
- **Зберегти зворотну сумісність.** Існуючі модулі з `widget.html` продовжують працювати. Нова система додається зверху.

### 1.3 Не-цілі

Рекрафт **не** торкається:

- Module Bus, EventBus або контрактів API ядра.
- Механізму kiosk-безпеки ([useConnectionHealth](../../src/hooks/useConnectionHealth.ts), 5-хвилинне перезавантаження при відсутності зв'язку).
- Системи розмірів сітки віджетів (`WxH` у маніфесті) — розміри переюзаються.
- PWA, Service Worker або HTTPS-проксі.
- Автентифікації, ACL чи дозволів.

---

## 2. Візуальний шар

### 2.1 Структура сторінки

Панель керування стає вертикальним стеком чотирьох регіонів у такому порядку:

```
┌──────────────────────────────────────────────────────────────┐
│  Hero — привітання, час, status pill, зовнішня погода         │  ~96 px
├──────────────────────────────────────────────────────────────┤
│  Сцени — 4 горизонтальні ярлики сцен                          │  ~52 px
├──────────────────────────────────────────────────────────────┤
│  Кімнати — Усі / Вітальня / Спальня / Кухня / Система         │  ~36 px
├──────────────────────────────────────────────────────────────┤
│                                                                │
│  Bento-сітка — віджети змішаних розмірів, grid-auto-flow dense │  заповнює
│                                                                │
└──────────────────────────────────────────────────────────────┘
```

Загальна висота не-сіткового chrome — ~184 px. На 1080p kiosk залишається ~880 px для віджетів — комфортно для двох рядків плиток 4×2. На телефоні (375×667) chrome стискається до ~140 px і сітка перемикається на одну колонку.

### 2.2 Hero-панель

| Регіон             | Зміст                                              |
|--------------------|----------------------------------------------------|
| Верхній-лівий рядок | День тижня, дата, час (наприклад «Понеділок, 27 квітня · 14:32») |
| Привітання         | «Доброго {ранку, дня, вечора}, {firstName}»        |
| Status pill        | Агрегований стан системи — див. §2.2.1             |
| Верхній-правий     | Зовнішня температура + опис погоди (якщо встановлено модуль `weather`) |

**Time-of-day фон.** Hero-регіон має м'який радіальний градієнт, відтінок якого ротується з локальною годиною: холодний синій з 22:00 до 06:00, теплий бурштиновий з 06:00 до 10:00, нейтральний біло-синій з 10:00 до 18:00, золотий з 18:00 до 22:00. Градієнт — `radial-gradient(ellipse at 20% 0%, var(--hero-tint) 0%, transparent 60%)` поверх `var(--bg)`. `--hero-tint` встановлюється на `<html data-tod="...">` і оновлюється кожні 15 хвилин.

#### 2.2.1 Стани status pill

Status pill — найважливіший індикатор на панелі. Це місце для існуючого здоров'я `IntegrityAgent`, подій `module.error` та банера SAFE MODE. Pill має чотири стани:

| Стан       | Колір          | Текст                                       | Тригер                                          |
|------------|----------------|---------------------------------------------|-------------------------------------------------|
| OK         | `--gr` (зел.)  | «Усі системи в нормі · {N} модулів активних» | Усі модулі `RUNNING`, integrity OK              |
| Warning    | `--am` (бурш.) | «Перевіряю цілісність...»                   | Триває перевірка цілісності                     |
| Degraded   | `--am` (бурш.) | «Модуль {name} у стані помилки»             | Будь-який модуль в `ERROR`, ядро здорове        |
| Safe       | `--rd` (черв.) | «SAFE MODE — {причина}»                     | Ядро активувало SAFE MODE (rollback / freeze)   |

Pill не клікабельний у стані OK і веде на `/settings/system-info` у решті трьох.

### 2.3 Ярлики сцен

Чотири ярлики сцен сидять під hero в одному горизонтальному ряду. Кожен ярлик — кнопка з іконкою + лейблом. Клік відправляє `POST /api/v1/scenes/{id}/activate` до існуючого `automation_engine`. Чотири сцени:

- **Доброго ранку** — світло на тепло-низько, клімат у комфорт, ранкове TTS-резюме
- **Я пішов** — усе світло вимк., клімат в away mode, охорона активована
- **Кіно** — світло у вітальні до 15 %, TV/медіа-плеєр увімкнено, AC у тихий режим
- **Спокійної ночі** — усе світло вимк., клімат у спальні в sleep mode, охорона активована

Якщо `automation_engine` не повертає сцен (свіжа інсталяція, немає визначених автоматизацій), ряд сцен повністю прихований. Він не показує порожні плейсхолдери.

### 2.4 Вкладки кімнат

Горизонтальний ряд фільтрів кімнат зі скролом при overflow. Вкладки виводяться під час виконання з полів `room` зареєстрованих пристроїв та модулів. Перша вкладка завжди «Усі», остання завжди «Система» (показує лише модулі з `room: "system"` — `cloud-sync`, `integrity`, `device-watchdog` тощо для діагностичних потреб господаря дому без винесення цього на гостей).

Стан вкладки зберігається у клієнтському `useState`. Він не персистує між перезавантаженнями — відкриття панелі завжди починається на «Усі». Це збігається з ментальною моделлю розумного дому: поверхня для поточного моменту, а не для продовження з місця.

### 2.5 Фіксована сітка 5×4

Після польових випробувань bento auto-flow панель повернулась до V1-сітки: **5 колонок × 4 рядки на десктопі / 1080p kiosk**, **4 колонки на планшеті (480–900 px)**, **одна колонка з вертикальним скролом на телефоні (<480 px)**. `grid-column: span W; grid-row: span H` обчислюється з manifest `WxH`; anchor-клітинка береться з `widgetLayout.positions[name]` (V1-карта переюзана — `slot = (col-1) + (row-1)*5`).

Розмір клітинки адаптивний: `cellHeight = (availableH − gaps) / 4`, `cellWidth = (availableW − gaps) / 5`. У edit-режимі рендеряться порожні слоти як `+` drop-таргети + м'які пунктирні лінії сітки, щоб користувач бачив куди приземлиться віджет при drag.

`grid-auto-flow: dense` лишається на контейнері — щоб out-of-bounds віджет (наприклад після зміни manifest size) не лишав дірки, але типовий випадок — повністю явне розміщення.

### 2.6 Дизайн-токени

Усі необхідні базові токени вже існують у [src/index.css](../../src/index.css). Рекрафт додає:

| Токен              | Темне значення                            | Світле значення                       | Призначення                                 |
|--------------------|--------------------------------------------|---------------------------------------|---------------------------------------------|
| `--hero-tint`      | динамічне (див. §2.2)                      | динамічне (світліше)                  | Time-of-day фон hero                        |
| `--widget-glow-on` | `0 0 0 1px var(--ac), 0 8px 24px rgba(90,150,255,.12)` | `0 0 0 1px var(--ac), 0 4px 16px rgba(59,122,232,.10)` | Підсвічування активного toggle-стану |
| `--motion-spring`  | `cubic-bezier(.5, 1.4, .5, 1)`             | (те ж саме)                           | Крива анімації toggle / mode-switch         |
| `--skeleton-bg`    | `linear-gradient(90deg, var(--sf2) 0%, var(--sf3) 50%, var(--sf2) 100%)` | (аналогічне) | Пульсуючий фон завантаження                 |

Існуючі токени (`--bg`, `--sf`, `--ac`, `--gr`, `--am`, `--rd`, `--tx`, `--tx2`, `--tx3` тощо) лишаються незміненими.

---

## 3. Шаблонний движок віджетів

### 3.1 Концепція

Зараз віджет визначається як «HTML-файл, відрендерений в iframe». Рекрафт ділить це на два види:

- **`kind: "template"`** — Модуль декларує **що** показати, повертаючи JSON-payload, що відповідає одній з п'яти вбудованих форм. Ядро рендерить UI. ~85 % реальних віджетів сюди вписуються.
- **`kind: "custom"`** — Модуль постачає HTML-файл, який рендериться в iframe — точно як сьогодні. Використовується для d3-візуалізацій, canvas UI, редакторів планів кімнат, ігор — будь-що поза поверхнею шаблонів.

Обидва види ділять той самий зовнішній chrome (status-точка, заголовок, меню), який малює ядро. Обидва ділять той самий життєвий цикл (skeleton → дані → оновлення → помилка). Обидва шанують фільтрацію за `room` та розмір `WxH`.

### 3.2 Схема маніфеста

Pydantic-схема: [`core/module_loader/manifest_schema.py`](../../core/module_loader/manifest_schema.py). Блок `ui.widget` отримує такі поля:

```json
{
    "ui": {
        "icon": "icon.svg",
        "widget": {
            "kind": "template",
            "template": "control-panel",
            "data_endpoints": {
                "state": {"path": "/widget/data/state", "cache_ttl_s": 5}
            },
            "actions": {
                "set_mode": {"path": "/widget/action/mode"},
                "set_temp": {"path": "/widget/action/temp"}
            },
            "size": "4x2",
            "max_size": "4x2",
            "refresh": {
                "events": ["device.state_changed"],
                "poll_interval_s": 30
            }
        },
        "settings": "settings.html"
    }
}
```

| Поле                              | Тип                      | Обов'язкове           | Опис                                                                                              |
|-----------------------------------|---------------------------|-----------------------|---------------------------------------------------------------------------------------------------|
| `widget.kind`                     | `"template" \| "custom"`  | Ні, дефолт `custom`   | Визначає, чи рендерить ядро, чи передає в iframe.                                                |
| `widget.template`                 | enum (див. §3.3)          | Якщо `kind="template"`| Який вбудований шаблон рендерити.                                                                 |
| `widget.data_endpoints[k]`        | `{path, cache_ttl_s?}`    | Ні                    | Шлях на HTTP-поверхні модуля (через Module Bus). Дашборд хитає `GET /api/v1/modules/{name}/data/{k}`. |
| `widget.actions[k]`               | `{path}`                  | Ні                    | Шлях для write-операцій. Дашборд хитає `POST /api/v1/modules/{name}/action/{k}`.                 |
| `widget.refresh.events`           | `string[]`                | Ні                    | Топіки EventBus, що тригерять refetch.                                                           |
| `widget.refresh.poll_interval_s`  | int (≥1)                  | Ні                    | Інтервал поллінгу як запасний.                                                                   |
| `widget.file`                     | string                    | Якщо `kind="custom"`  | HTML-файл для iframe. Ігнорується при `kind="template"`.                                         |

Існуючі `widget.size` та `widget.max_size` не змінюються. Pydantic-схема валідує `size` проти `template`: `metric` не може бути `4x4`; `control-panel` не може бути `1x1`; `status` не може бути більшим за `4x2`.

**Required `room`.** Phase 0 додає **обов'язкове** поле верхнього рівня `room: str`. Усі 18 системних модулів отримали `"room": "system"` (діагностичні) або `"room": "home"` (кросс-кімнатні UX-агрегатори).

### 3.3 Шаблони

Поточний набір — **8 шаблонів**: 5 generic-примітивів (3.3.1–3.3.5) і 3 спеціалізовані layout-и для частих rich-кейсів (3.3.6–3.3.8). Кожен шаблон специфікує: призначення, рекомендовані розміри, схему payload, контракт actions, гарантії рендеру.

Усі шаблони приймають імена іконок як звичайні рядки. Frontend-helper [`Icon`](#37-icon-system) резолвить їх у emoji-мапу (☀️ 🌧️ 💡 🔌 ⚡ 🎵 🎙️ 📡 🛡️ ...) — для дашборд-віджетів SVG-icon-бібліотека не bundle-иться. Модулі постачають lucide-style імена (`cloud`, `droplets`, `lightbulb`), helper підбирає glyph; невідомі імена fall-back-ять на `fallback` або саме ім'я.

#### 3.3.1 `metric`

Одне основне число з опціональним індикатором тренду та суфіксом одиниці. **Розміри:** `1x1`, `2x1` (бажано), ніколи більше за `2x2`.

```json
{
    "label": "Пристрої",
    "value": "14",
    "unit": null,
    "trend": {"direction": "up", "magnitude": "+2", "period": "за тиждень"},
    "tone": "neutral"
}
```

| Поле  | Опис |
|-------|------|
| `label`, `value`, `unit` | Заголовок, значення, суфікс |
| `trend.direction` | `"up" \| "down" \| "flat"` — іконка та колір |
| `trend.magnitude`, `trend.period` | Попередньо відформатовані стрічки |
| `tone` | `"neutral" \| "info" \| "ok" \| "warn" \| "alert"` |

**Опціональні поля Phase 6:** `icon` (lucide-style ім'я, рендериться у верхньому правому куті). Використовують `device-watchdog` (`activity`), `clock` (`alarm-clock`), `automation-engine` (`workflow`), `satellite-manager` (`satellite`).

**Actions:** немає.

#### 3.3.2 `sparkline`

Основне значення плюс маленький лінійний графік останніх N точок. **Розміри:** `2x1`, `2x2` (бажано), `4x2`.

```json
{
    "label": "Енергія", "value": "1.24", "unit": "кВт",
    "footnote": "сьогодні · 8.7 кВт·год",
    "series": [0.8, 0.9, 0.85, 1.1, 1.0, 1.3, 1.15, 1.5, 1.2, 1.4, 1.1, 1.24],
    "series_window_s": 3600,
    "tone": "info"
}
```

`series` ≤ 60 точок для візуальної ясності. Sparkline масштабується автоматично, межі Y `[min, max]` з 8 % padding, без сітки. Endpoint-крапка на «зараз» якорить останнє значення; gradient-fill keyed off `tone` темнішає до нуля.

**Опціональні поля Phase 6:**
- `icon` — leading glyph біля значення
- `breakdown: CardSpec[]` — топ-N contributor-карток нижче chart'а (use-case `energy-monitor`: топ-3 пристрої-споживачі поряд із загальним)

#### 3.3.3 `toggle-list`

Список іменованих toggle-елементів зі станом on/off та опціональною вторинною метрикою. **Розміри:** `2x2`, `4x2` (бажано), `4x4`.

```json
{
    "label": "Освітлення",
    "summary": "3 з 7 увімкнено",
    "items": [
        {"id": "living-1", "name": "Вітальня", "state": "on", "secondary": "80 %"},
        {"id": "kitchen-1", "name": "Кухня", "state": "on", "secondary": "100 %"},
        {"id": "hall-1", "name": "Коридор", "state": "off", "secondary": null}
    ]
}
```

**Actions:** `toggle` (обов'язковий) приймає `{"id": "<item_id>"}`. `set_secondary` (опц.) — long-press affordance для slider/picker.

**Опціональні поля Phase 6:** `items[].icon` — glyph per item. `lights-switches` мапить `entity_type → icon`: light → `lightbulb`, switch → `power`, outlet → `zap`. Неактивні items render-ять icon dim, активні — у accent-кольорі.

#### 3.3.4 `control-panel`

Основне значення, сегментований селектор режимів, опціональний ряд step-контролів. **Розміри:** `4x2` (бажано), ніколи менше за `2x2`.

```json
{
    "label": "Клімат · Вітальня",
    "primary": {"value": "22.5", "unit": "°", "secondary": "→ задано 23.0°"},
    "modes": {
        "current": "auto",
        "options": [
            {"id": "auto", "label": "Auto"},
            {"id": "cool", "label": "Cool"},
            {"id": "heat", "label": "Heat"},
            {"id": "dry",  "label": "Dry"}
        ]
    },
    "steppers": [
        {"id": "temp", "label": "Темп.", "value": "23.0", "unit": "°", "min": 16, "max": 30, "step": 0.5}
    ]
}
```

**Actions:** `set_mode` приймає `{"id": "<mode_id>"}`. `step` приймає `{"id": "<stepper_id>", "value": <number>}`.

**Опціональні поля Phase 6:** `secondary_pills: IconStripItem[]` — додаткові показники inline нижче primary. `climate` тут показує humidity / fan speed / estimated wattage — один віджет покриває те, що V1 розмазував по двох iframe.

#### 3.3.5 `status`

Pill здоров'я зверху, далі 1–4 рядки key-value. **Розміри:** `2x1`, `2x2` (бажано), не більше `4x2`.

```json
{
    "label": "Cloud sync",
    "pill": {"tone": "ok", "text": "Синхронізовано", "icon": "check"},
    "rows": [
        {"label": "Heartbeat", "value": "18s ago"},
        {"label": "Backoff",   "value": "5s"}
    ]
}
```

`pill.tone` ∈ {ok, info, warn, alert, neutral}. `pill.icon` приймає будь-яке ім'я з [Icon](#37-icon-system) (lucide-style; legacy short-codes `check / clock / alert / x / refresh` теж працюють). До 4 рядків. **Actions:** опц. `refresh`.

**Опціональні поля Phase 6:**
- `rows[].icon` — leading glyph для кожного рядка (наприклад `protocol-bridge` показує server-icon біля broker-host'а)
- `strip: IconStripItem[]` — компактний горизонтальний рядок icon + value; використовує `protocol-bridge` для здоров'я MQTT/Zigbee/Z-Wave
- `cards: CardSpec[]` — рядок mini-cards внизу; `notification-router` показує там превью останніх повідомлень
- `actions: ActionSpec[]` — inline кнопки в header'і; `update-manager` має `Check`, що тригерить upstream-перевірку через `POST /api/v1/modules/{name}/action/check_now`

#### 3.3.6 `weather` (спеціалізований)

Hero-condition + телеметричні pills + 3-денний forecast. **Розміри:** `4x2` (бажано), мін `2x2`.

```json
{
    "location": "Київ",
    "current": {
        "icon": "cloud-rain",
        "emoji": "🌧️",
        "temperature": 14,
        "unit": "°C",
        "condition": "Дощ",
        "feels_like": 12
    },
    "pills": [
        {"icon": "droplets", "value": "82%"},
        {"icon": "wind",     "value": "9 km/h"},
        {"icon": "cloud-rain", "value": "1.2 mm"}
    ],
    "forecast": [
        {"day": "Вт", "icon": "sun",        "high": 22, "low": 12, "unit": "°C"},
        {"day": "Ср", "icon": "cloud",      "high": 18, "low": 10, "unit": "°C"},
        {"day": "Чт", "icon": "cloud-rain", "high": 15, "low":  8, "unit": "°C"}
    ]
}
```

Рендериться як Apple-Weather-style hero (велика emoji + 38 px температура + condition · feels-like), icon-strip для телеметрії та 3 forecast-картки (high у `--am`, low у `--ac`). Hero має м'який radial-gradient, тонований за `current.icon` (sun → бурштиновий, cloud → прохолодно-сірий, cloud-rain → синій, zap → пурпурний). `current.emoji` — fallback коли `current.icon` невідомий.

**Actions:** немає.

#### 3.3.7 `media` (спеціалізований)

Cover art + track meta + transport row + volume slider. **Розміри:** `4x2` (бажано), мін `2x2`.

```json
{
    "state": "play",
    "track": {
        "title": "Hit FM",
        "artist": "Now playing",
        "album": null,
        "cover_url": "https://.../cover.jpg",
        "source_type": "radio",
        "duration_sec": null
    },
    "volume": 35,
    "position_sec": 12.5,
    "shuffle": false
}
```

Рендерить cover 64 px (повільно обертається при play), title + artist + source-type-бейдж, чотири круглі transport-кнопки (`previous` / `play` / `pause` / `next` — `play` більший і accent-glow коли активний), горизонтальний volume-slider зі speaker-іконкою.

`track` дорівнює `null` коли нічого не завантажено — шаблон показує cover-плейсхолдер, текст «Nothing playing» і dim-ить усі transport-кнопки крім `play`.

**Actions:**
- `set_mode` приймає `{"id": "play" | "pause" | "stop" | "previous" | "next"}` — викликає відповідний player-call
- `step` приймає `{"id": "volume", "value": <0..100>}`

#### 3.3.8 `presence` (спеціалізований)

Header pill + grid юзер-карток. **Розміри:** `2x1` (компактна summary), `2x2` (бажано), макс `4x2`.

```json
{
    "summary": {"tone": "info", "text": "2/3 home", "icon": "home"},
    "users": [
        {"id": "u1", "name": "Alice", "state": "home",    "last_seen": "just now"},
        {"id": "u2", "name": "Bob",   "state": "away",    "last_seen": "23m ago"},
        {"id": "u3", "name": "Eve",   "state": "unknown", "last_seen": null,
         "icon": "user-check", "badge": "guest"}
    ],
    "empty_text": "Користувачів ще нема"
}
```

Кожен user — карточка з gradient-аватаром (initial-літера або `icon`), status-кружок overlay (зелений/бурштиновий/сірий), name + tone-кольорове `last_seen` (або `Home`/`Away`/`—` fallback). Опціональний `badge` показується як маленький uppercase-чіп. Empty-state — центрована 👤 emoji + `empty_text`.

**Actions:** у базовому шаблоні немає; PIN-gated edit-flow живе у settings-сторінці модуля.

### 3.4 Custom-вид

Коли `kind: "custom"`, движок рендерить існуючий iframe-шлях з трьома доповненнями:

1. **Авто-інжект дизайн-токенів.** Движок пише `<style id="__selena_tokens">` у `<head>` iframe з усіма значеннями `--bg`, `--sf`, `--ac`, `--gr`, `--am`, `--rd`, `--tx{,2,3}` для поточної теми. Автор iframe пише `color: var(--tx)` і автоматично успадковує тему. При зміні теми движок переінжектує.
2. **Спільний chrome-обгортка.** iframe розміщується всередині того ж компонента `<WidgetChrome>`, що використовується шаблонами. Автори більше не пишуть свої заголовки.
3. **Типізований протокол postMessage:**

```ts
type WidgetMessage =
    | { type: "ready" }
    | { type: "modal_open"; module: string; width?: number; height?: number }
    | { type: "modal_close"; module: string }
    | { type: "modal_resize"; width: number; height: number }
    | { type: "open_settings"; module: string }
    | { type: "request_refresh" }
    | { type: "theme_changed"; theme: "dark" | "light" };
```

Існуючі повідомлення (`openWidgetModal`, `closeWidgetModal`, `openSettings`, `modal_resize`) аліасяться на одну minor-версію, потім видаляються.

### 3.5 Skeleton та error-стани

Кожен віджет проходить три стани: `loading → дані → оновлення... ↘ error ↙`.

**Loading.** Поки перша відповідь від `data_endpoints[k]` у польоті (або поки custom iframe не відправив `ready`), віджет рендерить skeleton за структурною формою шаблону. Skeleton'и зберігаються у `templates/Skeleton.tsx`.

**Error.** Якщо `data_endpoints[k]` повертає не 2xx, таймаут (>5 с), або iframe не відправив `ready` за 10 с, віджет рендерить error chrome: status-точка червона, body показує «Недоступно» з retry-кнопкою. Chrome-меню отримує «Показати деталі» з повідомленням про помилку.

### 3.6 Модель refresh

**Event-driven (бажано).** Маніфест декларує `refresh.events`. Движок підписується на ці топіки на WebSocket [`SyncManager`](../../core/api/sync_manager.py). Коли подія матчиться, движок refetch'ить `data_endpoints[k]`. Латентність ~50 мс.

**Polling (запасний).** Маніфест декларує `refresh.poll_interval_s`. Ловить дрейф, пропущені після reconnect події, та модулі без подій стану.

Обидва можуть співіснувати. Рекомендація: events для змін за write-тригерами, poll на 30 с для sensor-style даних. Для custom-віджетів — events форвардяться як `theme_changed` / `request_refresh` postMessage.

### 3.7 Icon-система

Модулі постачають іконки як рядкові імена. Helper [`Icon`](../../src/components/dashboard/templates/Icon.tsx) резолвить ім'я через emoji-таблицю і рендерить `<span>` із системним emoji-шрифтом (`Apple Color Emoji` / `Segoe UI Emoji` / `Noto Color Emoji` / `Twemoji Mozilla`). Для дашборд-віджетів SVG-icon-бібліотека не bundle-иться.

```tsx
<Icon name="cloud-rain" size={20} />          // → 🌧️
<Icon name="lightbulb" size={14} />           // → 💡
<Icon name="unknown-key" fallback="✦" />      // → ✦  (graceful fallback)
<Icon name={null} fallback="?" />             // → ?
```

Curated-набір (~50 імен) покриває категорії що використовуються сьогодні: weather (`cloud`, `cloud-rain`, `cloud-snow`, `sun`, `moon`, `wind`, `droplets`, `snowflake`, `thermometer`, `zap`), devices (`lightbulb`, `power`, `tv`, `radio`, `music`, `volume-2`, `mic`), system (`cpu`, `server`, `globe`, `network`, `wifi`, `bluetooth`, `satellite`, `settings`), people (`user`, `user-check`, `user-x`, `home`), status (`check`, `check-circle`, `clock`, `alert-triangle`, `x`, `refresh-cw`, `shield`, `sparkles`, `bell`, `eye`, `activity`), other (`alarm-clock`, `calendar`, `workflow`, `chevron-right`).

Додавання нових: розширити `EMOJI_MAP` у `Icon.tsx`. Імена слідують lucide-react конвенції (kebab-case), щоб майбутня міграція на векторні іконки була механічною.

### 3.8 Block-примітиви

Reusable React-компоненти у [`templates/blocks/`](../../src/components/dashboard/templates/blocks/), розшарені між спеціалізованими і generic-шаблонами. Module-автори не імпортують їх напряму — вони постачають payload-поля, які шаблони рендерять через ці примітиви.

| Примітив        | Використовується у                                   | Поле в payload     | Форма                                                                       |
|-----------------|------------------------------------------------------|--------------------|-----------------------------------------------------------------------------|
| `Pill`          | Status, Weather (forecast tone), Presence (summary) | implicit           | `{tone: PillTone, text: string, icon?: string}`                             |
| `IconStrip`     | Weather, Status, ControlPanel.secondary_pills        | `strip`, `pills`, `secondary_pills` | `{icon?, value: string, label?, tone?}[]`                                   |
| `CardRow`       | Weather (forecast), Sparkline (breakdown), Status (cards) | `cards`, `breakdown`, `forecast` | `{title?, value: string, secondary?, icon?, tone?}[]` — equal-width grid    |
| `ActionButton`  | Status (header)                                      | `actions`          | `{id: string, label: string, icon?, body?, tone?}` — POSTs до action-проксі |

`PillTone`, `IconStripItem`, `CardSpec`, `ActionSpec` — експортовані типи, точні інтерфейси у файлових заголовках.

---

## 4. Native vs iframe — матриця рішень

**Рішення: рендерити шаблонні віджети як React-компоненти у батьківському SPA.**

| Розгляд                        | iframe на віджет                  | React у батьківському                  |
|--------------------------------|------------------------------------|----------------------------------------|
| RAM на Pi (20 віджетів)        | ~140 MB (7 MB × 20)                | ~25 MB                                 |
| Латентність першого paint      | ~120 мс × N віджетів               | ~30 мс загалом                         |
| Консистентність анімацій       | неможлива — окремі timeline'и      | спільний timeline Framer Motion        |
| Hover / focus між віджетами    | неможливі                          | тривіальні                             |
| Перемикання теми               | реінжект токенів у N iframe        | каскад CSS-змінних                     |
| Гарантія ізоляції              | сильна (sandboxing іншого origin)  | слабша — потребує review trust-моделі  |

Вартість ізоляції має значення лише для **недовіреного third-party коду**. Шаблони приймають JSON від бекенда самого модуля — third-party HTML/CSS/JS не рендериться. Custom-віджети зберігають ізоляцію iframe тому що постачають довільний HTML/CSS/JS, включно з marketplace.

---

## 5. План міграції

### 5.1 Фаза 0 — підготовка (✅ виконано)

- ✅ Pydantic-схема [`core/module_loader/manifest_schema.py`](../../core/module_loader/manifest_schema.py).
- ✅ [`core/module_loader/validator.py`](../../core/module_loader/validator.py) делегує в Pydantic.
- ✅ Обов'язкове поле `room: str` додано до 18 системних маніфестів.
- ✅ `POST /api/v1/scenes/{id}/activate` ендпоінт + EventBus події `scene.activate`/`scene.activated`/`scene.failed`.
- ✅ `scene.*` додано у whitelist [`core/api/sync_bridge.py`](../../core/api/sync_bridge.py).
- ✅ Заглушки `core/api/routes/module_data.py` для `/api/v1/modules/{name}/data/{key}` та `/action/{key}`.
- ✅ Тести: `tests/test_manifest_schema.py`, `tests/test_scenes_activate.py`.

**Критерій виходу:** усі існуючі модулі продовжують стартувати; жодних UI-змін.

### 5.2 Фаза 1 — візуальний шар (5–7 днів)

**За feature flag `dashboardV2Enabled`. V1 лишається дефолтом.**

- Нові компоненти: `Hero.tsx`, `SceneRow.tsx`, `RoomTabs.tsx`, `BentoGrid.tsx`, `WidgetChrome.tsx`, `WidgetFrame.tsx`, `DashboardV2.tsx`.
- Refactor: drag/drop/resize/wobble логіка з [Dashboard.tsx](../../src/components/Dashboard.tsx) виноситься в `src/hooks/useBentoEdit.ts`.
- Додати токени `--hero-tint`, `--widget-glow-on`, `--motion-spring`, `--skeleton-bg` в [index.css](../../src/index.css).
- `useTimeOfDay()` хук — оновлює `<html data-tod="...">` кожні 15 хв.
- Зберегти роботу всіх існуючих iframe-віджетів.
- Білінгвальний текст для hero, сцен, room-вкладок.

**Критерій виходу:** з `?dashboardV2=1` панель візуально відповідає мокапу §2; усі існуючі віджети все ще завантажуються.

### 5.3 Фаза 2 — шаблонний движок + 2 шаблони (5–7 днів)

- `WidgetEngine.tsx`, `templates/Skeleton.tsx`, `templates/registry.ts`.
- Шаблони: `Metric`, `ToggleList`.
- Повна реалізація `core/api/routes/module_data.py` (Module Bus dispatch, TTL cache, 800 мс таймаут, stale-while-revalidate).
- `useWidgetData()` хук — fetch + EventBus subscribe + poll fallback.
- Міграція: `device-watchdog` → `metric`, `lights-switches` → `toggle-list`.

**Критерій виходу:** два реальних модулі рендеряться через шаблони; bento-сітка містить мікс шаблонних та iframe-віджетів.

### 5.4 Фаза 3 — решта шаблонів + 4 модулів (5–7 днів)

`Sparkline`, `ControlPanel`, `Status` + міграція `energy-monitor`, `climate`, `cloud-sync`, `integrity-agent`. Pydantic валідує `size` проти `template`.

### 5.5 Фаза 4 — поліровка custom-віджетів (3–4 дні)

- Авто-інжект дизайн-токенів у custom-iframe.
- Спільний chrome-wrapper.
- Типізований postMessage-контракт; deprecation warnings для старих імен.
- In-place міграція `widgetLayout` v1 → v2; `Reset layout` в Налаштуваннях.
- **Перемкнути `dashboardV2Enabled` дефолт на `true`.**

### 5.6 Фаза 5 — видалення V1 (✅ виконано)

- Видалено `widget.html` файли у всіх 13 мігрованих system-модулях.
- Видалено WidgetShell + DashboardV1-логіку з [Dashboard.tsx](../../src/components/Dashboard.tsx); файл став 9-рядковим wrapper'ом, що монтує `DashboardV2`.
- Прибрано `dashboardV2Enabled` opt-in (V2 — єдиний шлях).
- Агресивний `Cache-Control: no-store` на `/sw.js` та `/manifest.json` через raw ASGI middleware [`NoCacheForPaths`](../../core/api/middleware.py); `index.html` несе inline-скрипт unregister-всіх-SW + покинення caches, щоб legacy-SW із `/join` invite-flow більше не перехоплювали kiosk-перезавантаження.

### 5.7 Фаза 6 — спеціалізовані шаблони + emoji-first Icon (✅ виконано)

- 3 нові спеціалізовані шаблони: `weather`, `media`, `presence` — зареєстровані в [`templates/registry.ts`](../../src/components/dashboard/templates/registry.ts).
- [`Icon.tsx`](../../src/components/dashboard/templates/Icon.tsx) переписано як emoji-first (lucide-імпорти прибрано з dashboard-bundle, ~45-glyph мапа покриває усі поточні модулі).
- Block-примітиви у [`templates/blocks/`](../../src/components/dashboard/templates/blocks/): `Pill`, `IconStrip`, `CardRow`, `ActionButton`.
- Generic-шаблони отримали опціональні rich-слоти — `Metric.icon`, `Status.{cards, strip, actions, rows[].icon, pill.icon як lucide-name}`, `Sparkline.{icon, breakdown}`, `ControlPanel.secondary_pills`, `ToggleList.items[].icon`.
- Сітка 5×4 повернута з bento auto-flow (тестувався в Phase 1); явне розміщення через `widgetLayout.positions` + видимі пунктирні гайдлайни в edit-режимі.
- Усі 14 in-tree widget-endpoints видають Phase-6-shape payload-и. Pydantic-схема приймає нові template-name; додано size envelopes: `weather`/`media` ≥ 2×2, `presence` ≥ 2×1.
- `media-player` повертається на template (V1 widget.html, який ненадовго повернувся в Phase 5, видалено знову — новий `media`-шаблон покриває cover art + transport + volume scrubber нативно).

### 5.8 Гарантії зворотної сумісності

- Маніфести з `widget.kind` за замовчуванням лишаються `custom` — поточна iframe-поведінка.
- Існуючі файли `widget.html` завантажуються та рендеряться точно як раніше для `kind: "custom"` модулів.
- Усі Phase-6-поля на generic-шаблонах опціональні — старіші payload-и продовжують рендеритися (icon-слот рендериться нічим якщо відсутній).
- Status `pill.icon` приймає і legacy short codes (`check`, `clock`, `alert`, `x`, `refresh`), і будь-яке ім'я з [Icon](#37-icon-system).
- Прибрано: pre-Phase-4 postMessage-імена (`openWidgetModal`, `closeWidgetModal`, `openSettings`, `refresh`) — Phase 5 видалила аліаси. Custom-модулі мають використовувати канонічні `WidgetMessage`-типи з [`src/lib/widgetMessages.ts`](../../src/lib/widgetMessages.ts).

---

## 6. Карта компонентів

```
src/
├── components/
│   ├── Dashboard.tsx                  (9-рядковий wrapper — монтує DashboardV2)
│   └── dashboard/
│       ├── DashboardV2.tsx            (композиційний корінь)
│       ├── Hero.tsx                   (привітання + клок + status pill + погода)
│       ├── SceneRow.tsx               (чіпи → POST /scenes/{id}/activate)
│       ├── RoomTabs.tsx               (derived з manifest.room)
│       ├── BentoGrid.tsx              (фіксовані 5×4 з explicit positioning)
│       ├── WidgetChrome.tsx           (status-dot + edit-bar + resize-handle)
│       ├── WidgetFrame.tsx            (router template-registry, iframe-fallback)
│       ├── AddWidgetDrawer.tsx        (bottom-sheet для pin'ингу нових віджетів)
│       └── templates/
│           ├── Icon.tsx               (emoji-first; ~50-name lucide-style мапа)
│           ├── registry.ts            (8 шаблонів: 5 generic + 3 спеціалізовані)
│           ├── Skeleton.tsx           (один skeleton-варіант per template)
│           ├── Metric.tsx             (generic — primary + tone)
│           ├── Sparkline.tsx          (generic — value + chart + breakdown)
│           ├── ToggleList.tsx         (generic — Apple-Home tile-grid)
│           ├── ControlPanel.tsx       (generic — primary + modes + steppers)
│           ├── Status.tsx             (generic — pill + rows + cards/actions)
│           ├── Weather.tsx            (спеціалізований — hero + pills + forecast)
│           ├── Media.tsx              (спеціалізований — cover + transport + volume)
│           ├── Presence.tsx           (спеціалізований — avatar circles + state-dot)
│           └── blocks/
│               ├── Pill.tsx           (tone + text + icon)
│               ├── IconStrip.tsx      (icon + value горизонтально)
│               ├── CardRow.tsx        (equal-width grid mini-cards)
│               └── ActionButton.tsx   (POSTs /modules/{name}/action/{id})
├── store/
│   └── useStore.ts                    (Module.room, widgetLayout.version, swapWidgets V2)
├── hooks/
│   ├── useBentoEdit.ts                (drag-to-empty + drag-to-swap + resize)
│   ├── useWidgetData.ts               (fetch + EventBus subscribe + poll)
│   └── useTimeOfDay.ts                (data-tod атрибут кожні 15 хв)
├── lib/
│   └── widgetMessages.ts              (типізований postMessage-протокол)
└── index.css                          (--hero-tint, --widget-glow-on, --motion-spring, --skeleton-bg)

core/
├── module_loader/
│   ├── manifest_schema.py             (Pydantic — 8 template-name + size envelopes)
│   └── validator.py                   (делегування в Pydantic)
└── api/
    ├── middleware.py                  (NoCacheForPaths ASGI для /sw.js + /manifest.json)
    ├── routes/
    │   ├── module_data.py             (proxy /api/v1/modules/{name}/data|action/{key})
    │   ├── modules.py                 (ModuleResponse експонує room)
    │   └── scenes.py                  (POST /{id}/activate + scene.* events)
    └── sync_bridge.py                 (whitelist scene.activate / activated / failed)

system_modules/                        (всі 18 манифестів декларують room; 13 видають template-payload-и)
```

---

## 7. Відхилені альтернативи

**A. Чисто-React віджети, без iframe взагалі.** Розглянуто і відхилено. Marketplace-віджети постачають third-party JS/CSS — sandboxing має значення.

**B. Web Components у Shadow DOM замість iframe.** Розглянуто. Краще за iframe щодо продуктивності, гірше щодо ізоляції JS (Shadow DOM не ізолює `window`). Cross-browser-квирки на старіших WebKit (Pi browser kiosk).

**C. Custom-віджети на HTMX.** Розглянуто. SPA вже на React; введення другої парадигми додає складності. JSON-контракти шаблонів працюють для веба і майбутнього нативного клієнта без переробки.

**D. Glass morphism на світлій темі.** Контрастні співвідношення не проходять WCAG AA при 13 px-тексті. Світла тема — суцільні поверхні.

**E. Per-widget user-configurable backgrounds.** Кастомізація відтягує фокус від даних. Завдання панелі — бути читабельною, не персоналізованою.

---

## 8. Відкриті питання

1. **Multi-room віджети.** `control-panel` для клімату міг би показати три кімнати на плитці `4x4`. Опція з полем `siblings: ControlPanelPayload[]` чи окремий `multi-control-panel`? Схиляємось до першого.
2. **Композиція віджетів.** Прев'ю сцени з трьома toggle + температурою — template-of-templates чи custom? Custom поки що.
3. **Long-press affordance.** На kiosk-тачскрині природно; на миші незручно. Right-click? Modifier-click? `⋯` на елемент? Вирішити в Фазі 2.
4. **i18n payload-лейблів.** Через `Accept-Language` хедер чи `?lang=` параметр? Хедер чистіше.
5. **UI керування сценами.** «+ Додати сцену» в edit-режимі? Поза скоупом, але прапорцем.
6. **Поведінка mobile-breakpoint.** Стискати hero + переносити сцени у два ряди, чи скролл? Стиснення.

---

## 9. Посилання

- [`widget-development.md`](widget-development.md) — поточний посібник з віджетів. Переписаний в Фазі 4.
- [`architecture.md`](architecture.md) — архітектурний огляд SelenaCore.
- [`ui-sync-architecture.md`](ui-sync-architecture.md) — WebSocket sync-протокол.
- [`provider-system-and-modules.md`](provider-system-and-modules.md) — аналогічний документ архітектурного рекрафта.
- [`module-development.md`](module-development.md) — SDK-довідка для авторів модулів.
