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

### 2.5 Bento-сітка

Сітка — `display: grid; grid-template-columns: repeat(N, minmax(0, 1fr)); grid-auto-flow: dense; gap: 10px`, де N залежить від екрана (3 на планшеті, 4 на десктопі, 6 на 1080p kiosk, 1 на телефоні). CSS кожного віджета `grid-column: span W; grid-row: span H` використовує оголошений у маніфесті розмір `WxH`.

`dense`-flow свідомий: дозволяє браузеру заповнювати ранні пропуски пізнішими маленькими віджетами, видаючи щільніший bento-layout без ручного розміщення.

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

Кожен шаблон специфікує: призначення, рекомендовані розміри, схему payload, контракт actions та гарантії рендеру.

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

`series` ≤ 60 точок для візуальної ясності. Sparkline масштабується автоматично, межі Y `[min, max]` з 8 % padding, без сітки.

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

`pill.tone` ∈ {ok, info, warn, alert, neutral}. `pill.icon` ∈ {check, clock, alert, x, refresh}. До 4 рядків. **Actions:** опц. `refresh`.

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

### 5.6 Фаза 5 — видалення V1 (постійно)

- Видалити `widget.html` файли з усіх системних модулів.
- Видалити WidgetShell-логіку з [Dashboard.tsx](../../src/components/Dashboard.tsx); файл стає тонким wrapper над DashboardV2 або видаляється.
- Видалити iframe-шлях з `WidgetFrame.tsx`. Marketplace-модулі продовжують через `kind: "custom"`.
- Видалити старі postMessage-аліаси.

### 5.7 Гарантії зворотної сумісності

- Маніфести з `widget.kind` за замовчуванням лишаються `custom` — поточна iframe-поведінка.
- Існуючі файли `widget.html` завантажуються та рендеряться точно як раніше.
- Старі імена postMessage працюють у custom-режимі з deprecation warning одну major-версію.
- Фіксована сітка 5×4 заміняється `auto-flow: dense`, але розміри віджетів (`WxH`) шануються точно.

---

## 6. Карта компонентів

```
src/
├── components/
│   ├── Dashboard.tsx               (refactor: feature-flag-бранч)
│   └── dashboard/
│       ├── DashboardV2.tsx         (новий)
│       ├── Hero.tsx                (новий)
│       ├── SceneRow.tsx            (новий)
│       ├── RoomTabs.tsx            (новий)
│       ├── BentoGrid.tsx           (новий)
│       ├── WidgetChrome.tsx        (новий)
│       ├── WidgetFrame.tsx         (новий — iframe + template router)
│       └── templates/
│           ├── Metric.tsx          (новий)
│           ├── Sparkline.tsx       (новий)
│           ├── ToggleList.tsx      (новий)
│           ├── ControlPanel.tsx    (новий)
│           ├── Status.tsx          (новий)
│           ├── Skeleton.tsx        (новий)
│           └── registry.ts         (новий)
├── store/
│   └── useStore.ts                 (extend: dashboardV2Enabled, widgetLayout.version)
├── hooks/
│   ├── useBentoEdit.ts             (новий — лифт drag/drop/resize)
│   ├── useWidgetData.ts            (новий)
│   └── useTimeOfDay.ts             (новий)
└── index.css                       (нові токени з §2.6)

core/
├── module_loader/
│   ├── manifest_schema.py          ✅ Phase 0
│   └── validator.py                ✅ Phase 0
└── api/
    ├── routes/
    │   ├── module_data.py          ✅ Phase 0 (заглушка → Phase 2 повна)
    │   └── scenes.py               ✅ Phase 0 (+ /activate)
    └── sync_bridge.py              ✅ Phase 0 (+ scene.* whitelist)

system_modules/                     ✅ усі 18 манифестів отримали room
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
