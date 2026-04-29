# Посібник з розробки віджетів

Цей посібник описує створення UI-віджетів, сторінок налаштувань та іконок для модулів SelenaCore.

> **Спочатку шаблонний движок.** Після рекрафта панелі керування (Phase 5/6
> у мейнстрімі) основний шлях для нових віджетів — **шаблонний движок**:
> декларуєш payload-форму в маніфесті, і панель сама рендерить. Iframe з
> власним HTML — запасний варіант, коли жоден із 8 вбудованих шаблонів
> не підходить. Дивись [dashboard-recraft.md](dashboard-recraft.md) §3 —
> **5 generic-шаблонів** (`metric`, `sparkline`, `toggle-list`,
> `control-panel`, `status`) і **3 спеціалізовані** (`weather`, `media`,
> `presence`), плюс контракт `data_endpoints` / `actions`, emoji-first
> [Icon-система](dashboard-recraft.md#37-icon-система) та reusable
> [block-примітиви](dashboard-recraft.md#38-block-примітиви) (Pill /
> IconStrip / CardRow / ActionButton).
>
> Цей документ покриває налаштування манифеста, settings-сторінки,
> іконки і **`kind: "custom"` iframe-віджети** для рідкісних випадків
> коли шаблон не підходить. Phase 5 видалила legacy postMessage-імена
> (`openWidgetModal`, `closeWidgetModal`, `openSettings`, `refresh`) —
> використовуй канонічні імена з
> [`src/lib/widgetMessages.ts`](../../src/lib/widgetMessages.ts). Модулі
> та інтерфейси конфігурації обслуговуються ядром за адресою
> `/api/ui/modules/{module_name}/`.

---

## Зміст

1. [UI-профілі](#ui-профілі)
2. [Секція UI у manifest.json](#секція-ui-у-manifestjson)
3. [Розміри сітки](#розміри-сітки)
4. [Спільна бібліотека компонентів](#спільна-бібліотека-компонентів)
5. [HTML-структура віджета](#html-структура-віджета)
6. [Сторінка налаштувань](#сторінка-налаштувань)
7. [Шаблони комунікації](#шаблони-комунікації)
8. [Вимоги до іконок](#вимоги-до-іконок)
9. [Повний приклад UI модуля](#повний-приклад-ui-модуля)
10. [Найкращі практики](#найкращі-практики)

---

## UI-профілі

Кожен модуль декларує свою UI-присутність через поле `ui_profile` у `manifest.json`. Оберіть профіль, який відповідає потребам вашого модуля:

| Профіль          | Іконка | Віджет | Сторінка налаштувань |
|------------------|--------|--------|----------------------|
| `HEADLESS`       | Ні     | Ні     | Ні                   |
| `SETTINGS_ONLY`  | Ні     | Ні     | Так                  |
| `ICON_SETTINGS`  | Так    | Ні     | Так                  |
| `FULL`           | Так    | Так    | Так                  |

Фоновий сервіс без елементів керування для користувача повинен використовувати `HEADLESS`. Модуль, якому потрібна конфігурація, але без присутності на панелі, повинен використовувати `SETTINGS_ONLY`. Більшість інтерактивних модулів використовують `FULL`.

---

## Секція UI у manifest.json

Кожен manifest декларує верхнього рівня поле `room` (обов'язкове з Phase 0 — `"system"` для діагностичних модулів, `"home"` для cross-room user-facing-агрегаторів, або власне ім'я кімнати) і опціональний блок `ui`.

### Template-віджет (бажано — 13/14 in-tree-модулів)

```json
{
    "room": "home",
    "ui": {
        "icon": "icon.svg",
        "widget": {
            "kind": "template",
            "template": "control-panel",
            "size": "4x2",
            "max_size": "4x2",
            "data_endpoints": {
                "state": {"path": "/widget/data/state", "cache_ttl_s": 5}
            },
            "actions": {
                "set_mode": {"path": "/widget/action/mode"},
                "step":     {"path": "/widget/action/temp"}
            },
            "refresh": {
                "events": ["device.state_changed"],
                "poll_interval_s": 30
            }
        },
        "settings": "settings.html"
    }
}
```

Панель рендерить React-компонент, що відповідає `template`. Вибирай з 8 вбудованих імен: `metric`, `sparkline`, `toggle-list`, `control-panel`, `status`, `weather`, `media`, `presence`. Кожен має payload-схему задокументовану в [dashboard-recraft.md §3.3](dashboard-recraft.md#33-шаблони).

### Custom (iframe) віджет — fallback

`kind: "custom"` тільки коли жоден з 8 шаблонів не підходить (canvas-візуалізації, room-plan-редактори, embedded-ігри):

```json
{
    "room": "home",
    "ui": {
        "icon": "icon.svg",
        "widget": {
            "kind": "custom",
            "file": "widget.html",
            "size": "2x2",
            "max_size": "4x4"
        },
        "settings": "settings.html"
    }
}
```

### Опис полів

| Поле                            | Тип     | Обов'язкове         | Опис                                                                                  |
|---------------------------------|---------|---------------------|---------------------------------------------------------------------------------------|
| `room`                          | string  | Так                 | Тег кімнати — формує room-фільтр панелі. `"system"` для не-user-facing-діагностики.   |
| `ui.icon`                       | string  | Ні                  | Шлях до SVG-файлу іконки (відносно кореня модуля).                                    |
| `ui.widget.kind`                | enum    | Ні, дефолт custom   | `"template"` для движка; `"custom"` для iframe-widget.html.                           |
| `ui.widget.template`            | enum    | Якщо kind=template  | Одне з 8 імен шаблонів. Дивись dashboard-recraft.md §3.3.                             |
| `ui.widget.data_endpoints[k]`   | `{path, cache_ttl_s?}` | Ні       | Шлях на HTTP-поверхні модуля; панель хитає `GET /api/v1/modules/{name}/data/{k}`.     |
| `ui.widget.actions[k]`          | `{path}`| Ні                  | Шлях для write-actions; панель хитає `POST /api/v1/modules/{name}/action/{k}`.        |
| `ui.widget.refresh.events`      | string[]| Ні                  | EventBus-топіки що тригерять refetch (наприклад `device.state_changed`).              |
| `ui.widget.refresh.poll_interval_s` | int (≥1) | Ні             | Запасний інтервал поллінгу в секундах.                                                |
| `ui.widget.file`                | string  | Якщо kind=custom    | HTML-файл для iframe. Ігнорується при `kind: "template"`.                             |
| `ui.widget.size`                | string  | Ні                  | Розмір сітки за замовчуванням (`"ШxВ"`, напр. `"4x2"`).                               |
| `ui.widget.max_size`            | string  | Ні                  | Максимальний розмір сітки (V2-панель — фіксована 5×4, span clamped).                  |
| `ui.settings`                   | string  | Ні                  | HTML-файл сторінки налаштувань модуля.                                                |

Усі шляхи до файлів — відносні до кореневого каталогу модуля.

---

## Розміри сітки

Панель керування використовує сіткову розкладку. Віджети займають комірки, визначені як `ШиринаxВисота`:

| Розмір | Опис                  | Випадок використання                    |
|--------|-----------------------|-----------------------------------------|
| `1x1`  | Малий квадрат         | Відображення одного значення, перемикач |
| `2x1`  | Широкий малий         | Значення з підписом, компактний статус  |
| `1x2`  | Високий малий         | Вертикальний список, мала діаграма      |
| `2x2`  | Середній квадрат      | Основний розмір віджета (за замовчуванням) |
| `4x2`  | Широкий великий       | Графіки, багатозначні панелі            |
| `4x4`  | Повний великий        | Складні елементи керування, відеопотоки |

Встановіть `size` як розмір за замовчуванням, а `max_size` як найбільший розмір, до якого віджет може масштабуватися. Панель керування не дозволить користувачам змінювати розмір більше за `max_size`.

---

## Спільна бібліотека компонентів

Кожен віджет і сторінка налаштувань виконуються всередині iframe і завантажують два спільні ресурси від ядра. **Завжди підключайте їх у `<head>`:**

```html
<link rel="stylesheet" href="/api/shared/theme.css">
<script src="/api/shared/widget-common.js"></script>
```

Це дає повну бібліотеку компонентів — токени теми, картки, кнопки, форми, бейджі, toast-сповіщення, модальні вікна, перемикачі, чіпи, індикатори статусу — плюс JS-хелпери для `fetch`, сповіщень, станів завантаження, вкладок і локалізації. **У більшості випадків вашому модулю взагалі не потрібен блок `<style>`.**

Канонічні стартові шаблони: [`docs/module-ui-template/widget.template.html`](../module-ui-template/widget.template.html) та [`docs/module-ui-template/settings.template.html`](../module-ui-template/settings.template.html). Скопіюйте один і правте.

### Автоматичний layout для body

`widget-common.js` автоматично встановлює клас для `<body>` на основі імені файлу:

| Файл | Автоклас | Ефект |
|---|---|---|
| `widget.html` | `body.sc-widget` | Прозорий фон, без прокрутки — органічно вписується у плитку панелі. |
| `settings.html` | `body.sc-settings` | Відступ `20px`, прокрутка, максимальна ширина `800px`, центровано. |

Якщо модулю потрібен інший layout, встановіть один із цих класів на `<body>` самі — автовиконавець не буде змінювати ваш вибір. Щоб повністю відмовитися від auto-apply (у вас власний повноекранний body з власним фоном, padding або sticky-заголовком, для якого жоден preset не підходить), використовуйте `<body class="sc-custom">`.

### Короткий довідник компонентів

| Призначення | Клас або елемент |
|---|---|
| Контейнер-картка | `.card` (основна), `.card-inner` (вкладена) |
| Заголовок секції | `h2` + `.subtitle`, або `.section-title` + `.section-sub` |
| Мала мітка / підказка | `.label-sm`, `.label-xs` |
| Основна кнопка дії | `.btn .btn-primary` (синонім: `.btn-blue`) |
| Другорядна кнопка | `.btn .btn-secondary` або `.btn .btn-outline` |
| Деструктивна кнопка | `.btn .btn-danger` (м'яка) / `.btn-danger-solid` (заливка) |
| Успіх-кнопка | `.btn .btn-green` |
| Прозора / посилання | `.btn .btn-ghost`, `.btn-link` |
| Кнопка-іконка | `.icon-btn` (+ `.icon-btn-sm` / `.icon-btn-lg`) |
| Поле форми (label + input) | `.field`, що містить `<label>` + `<input>` |
| Дворядкова форма | `.field-row`, що містить два `.field` |
| Текстові поля | `input[type="text|number|password"]`, `textarea`, `select` — уже стилізовані |
| Слайдер | `input[type="range"]` + `.slider-row` / `.slider-header` |
| Перемикач | `.toggle` (містить `<input type="checkbox">` + `.slider`) |
| Вибір чіпів | `.chip-picker` + `.chip` (з `.on` / `.active`) |
| Статусна піґулка | `.badge` + `.badge-ok` / `-err` / `-warn` / `-info` / `-pr` |
| Статусна крапка | `.status-dot` + `.ok` / `.warn` / `.err` / `.info` |
| Смуга вкладок | `.settings-tabs` + `.settings-tab` + `.tab-panel` (виклик `initTabs()`) |
| Таблиця даних | `<table>` (уже стилізована — клас не потрібен) |
| Прогрес-бар | `.progress-bar` + `.progress-bar .fill` |
| Toast-сповіщення | Виклик `showToast(msg, 'success'|'error'|'info')` |
| Модальне вікно | `.modal-overlay` > `.modal` |
| Нижній sheet-редактор | `.sheet-overlay` > `.sheet` + `.sheet-actions` |
| Список рядків | `.list` > `.list-row` (+ `.clickable` / `.off`) |
| Пустий стан | `.empty-state` + `.es-title` |
| Плаваюча кнопка дії | `.fab` |
| Фіксована сітка статистики | `.stat-grid` + `.stat-card` + `.num` + `.desc` |
| Загальна сітка | `.grid-2`, `.grid-3`, `.grid-4`, `.grid-auto` |
| KPI-блок (число + підпис) | `.kpi` > `.kpi-val` (+ `-accent` / `-success` / `-warn` / `-danger`) + `.kpi-lbl` |
| Вертикальний ритм | `.stack` / `.stack-sm` / `.stack-lg` на контейнері |
| Горизонтальний ряд | `.row`, `.flex`, `.flex-col`, `.wrap`, `.flex1` |
| Утиліти відступів | `.gap4` … `.gap16`, `.mb4` … `.mb16`, `.mt4` … `.mt16` |
| Спінер / скелетон | `.spinner`, `.skeleton`, `.pulse` |
| Розділювач | `.divider-dashed` |
| Сховати елемент | `.hidden` |
| Моноширинний текст | `.mono` |

### JS-хелпери (з `widget-common.js`)

```js
// Fetch — BASE обчислюється автоматично, auth-заголовки додаються автоматично
apiGet('/status').then(data => { … });
apiPost('/settings', { city: 'Kyiv' }).then(() => { … });
apiDelete('/items/42');
apiPatch('/config', { enabled: true });

// Toast (транслюється й на батьківську панель)
showToast('Збережено', 'success');
showToast('Помилка з’єднання', 'error');
showToast('Перезапуск…', 'info');

// Стан завантаження кнопки — вимикає кнопку, показує спінер,
// ловить помилки і показує їх у toast автоматично
withLoading(btnElement, () => apiPost('/action'));

// DOM-хелпери
$('my-id');        // document.getElementById
show('my-id');     // знімає .hidden
hide('my-id');
esc(userString);   // HTML-екранування перед innerHTML

// Перемикання вкладок — кнопки з [data-tab="x"] активують панель #tabX
initTabs();
```

### Локалізація

Визначте `L = { en: {...}, uk: {...} }`, потім розмітьте розмітку i18n-атрибутами:

```html
<h2 data-i18n="title"></h2>
<input data-placeholder-i18n="ph_name">
<button data-i18n="save" data-i18n-title="save_tip"></button>
<span data-i18n-aria-label="lbl_status"></span>
```

Викличте `applyLang()` один раз при завантаженні. Поточна мова — `LANG` (автоматично зчитується з `localStorage['selena-lang']`), `t(key)` повертає переклад. Коли користувач перемикає мову у батьківській панелі, повідомлення `lang_changed` через postMessage перезапускає `applyLang()` і викликає вашу функцію `refresh()` / `load()` / `loadStatus()`, якщо вона існує.

### Токени теми (лише для специфічних стилів модуля)

Якщо вам справді потрібен кастомний CSS для спеціалізованої візуалізації, використовуйте ці CSS custom properties — ваші стилі будуть слідувати активній темі:

| Токен | Призначення |
|---|---|
| `--bg` / `--sf` / `--sf2` / `--sf3` | Фонові шари (додаток → поверхня → підвищений → найглибший) |
| `--b` / `--b2` | Рамки (тонка → виразна) |
| `--tx` / `--tx2` / `--tx3` | Текст (основний → вторинний → третинний) |
| `--ac` | Акцент (синій) |
| `--gr` / `--am` / `--rd` | Семантичні кольори (успіх / попередження / небезпека) |
| `--on-accent` / `--on-success` / `--on-warning` / `--on-danger` | WCAG AA-парні кольори тексту для використання поверх насичених заливок |
| `--shadow` / `--shadow-lg` | М’яка / виразна тінь |

Усі токени автоматично змінюються між світлою/темною темами та адаптуються до режиму `has-wallpaper`. **Ніколи не вписуйте hex-кольори безпосередньо у CSS модуля.**

---

## HTML-структура віджета

> **Стосується лише `kind: "custom"`.** Для template-віджетів
> (`kind: "template"`) панель рендерить React-компонент із вашого
> JSON-payload — HTML-файл не потрібен. Дивись [dashboard-recraft.md
> §3.3](dashboard-recraft.md#33-шаблони) для payload-схем. Решта секції
> описує iframe-шлях для рідкісних випадків де шаблони не підходять.

Custom-віджети вбудовуються як iframe на панелі керування. Кожен віджет — самостійний HTML-файл, який завантажує спільну бібліотеку компонентів (див. попередню секцію) і додає специфічну для модуля розмітку та скрипт.

### Мінімальний приклад

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: transparent;
            color: var(--text-color, #333);
            padding: 12px;
        }
        .widget-title { font-size: 14px; font-weight: 600; margin-bottom: 8px; }
        .widget-value { font-size: 32px; font-weight: 700; }
        .widget-label { font-size: 12px; color: var(--text-secondary, #666); }
    </style>
</head>
<body>
    <div class="widget-title">Weather</div>
    <div class="widget-value" id="temp">--°C</div>
    <div class="widget-label" id="condition">Loading...</div>

    <script>
        // Communicate with module backend via parent window postMessage
        // or fetch from module API endpoint
        async function loadData() {
            try {
                const res = await fetch('/api/ui/modules/weather-module/current');
                const data = await res.json();
                document.getElementById('temp').textContent = data.temperature + '°C';
                document.getElementById('condition').textContent = data.condition;
            } catch (e) {
                document.getElementById('condition').textContent = 'Error loading data';
            }
        }
        loadData();
        setInterval(loadData, 60000); // Refresh every minute
    </script>
</body>
</html>
```

### Ключові моменти

- **Фон має бути `transparent`**, щоб віджет гармонійно вписувався у плитку панелі.
- **Вбудовуйте весь CSS та JS.** Зовнішні стилі та скрипти додають затримку та складність.
- **Встановіть мета-тег viewport**, щоб забезпечити правильне масштабування на всіх пристроях.
- **Використовуйте блок скидання стилів** (`* { margin: 0; padding: 0; box-sizing: border-box; }`), щоб уникнути невідповідностей стандартних стилів браузера всередині iframe.

---

## Сторінка налаштувань

Сторінки налаштувань дозволяють користувачам конфігурувати поведінку модуля. Вони відображаються у більшому вікні перегляду, ніж віджети, та можуть використовувати стандартні елементи форм.

### Приклад

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <style>
        body { font-family: sans-serif; padding: 16px; }
        .form-group { margin-bottom: 16px; }
        label { display: block; margin-bottom: 4px; font-weight: 600; }
        input, select { width: 100%; padding: 8px; border: 1px solid #ccc; border-radius: 4px; }
        button { padding: 8px 16px; background: #007AFF; color: white; border: none; border-radius: 4px; cursor: pointer; }
        button:hover { background: #005EC4; }
        .status { margin-top: 12px; font-size: 14px; }
        .status.success { color: #28a745; }
        .status.error { color: #dc3545; }
    </style>
</head>
<body>
    <h2>Weather Settings</h2>
    <div class="form-group">
        <label>City</label>
        <input type="text" id="city" value="Kyiv">
    </div>
    <div class="form-group">
        <label>Units</label>
        <select id="units">
            <option value="celsius">Celsius</option>
            <option value="fahrenheit">Fahrenheit</option>
        </select>
    </div>
    <button onclick="saveSettings()">Save</button>
    <div class="status" id="status"></div>

    <script>
        async function loadSettings() {
            try {
                const res = await fetch('/api/ui/modules/weather-module/settings');
                const data = await res.json();
                document.getElementById('city').value = data.city || '';
                document.getElementById('units').value = data.units || 'celsius';
            } catch (e) {
                showStatus('Failed to load settings', 'error');
            }
        }

        async function saveSettings() {
            const settings = {
                city: document.getElementById('city').value,
                units: document.getElementById('units').value,
            };
            try {
                const res = await fetch('/api/ui/modules/weather-module/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(settings),
                });
                if (res.ok) {
                    showStatus('Settings saved', 'success');
                } else {
                    showStatus('Save failed: ' + res.statusText, 'error');
                }
            } catch (e) {
                showStatus('Network error', 'error');
            }
        }

        function showStatus(message, type) {
            const el = document.getElementById('status');
            el.textContent = message;
            el.className = 'status ' + type;
        }

        loadSettings();
    </script>
</body>
</html>
```

Сторінки налаштувань завжди повинні:

1. **Завантажувати наявні значення при відкритті сторінки**, щоб користувач бачив поточну конфігурацію.
2. **Показувати зворотний зв'язок після збереження** (повідомлення про успіх або помилку).
3. **Валідувати введені дані на стороні клієнта** перед відправкою на бекенд.

---

## Шаблони комунікації

Спосіб взаємодії віджета з бекендом модуля залежить від типу модуля.

### Системні модулі (SYSTEM)

Системні модулі працюють всередині процесу ядра та надають REST-ендпоінти через `get_router()`. Віджети виконують запити безпосередньо до цих ендпоінтів:

```javascript
// Direct fetch to the module's registered routes
const res = await fetch('/api/ui/modules/{module-name}/endpoint');
const data = await res.json();
```

Автентифікація не потрібна. UI-маршрути доступні лише з localhost, захищені на мережевому рівні через iptables.

### Користувацькі модулі (User)

Користувацькі модулі працюють в окремих процесах та взаємодіють через Module Bus. Ядро діє як API-проксі, пересилаючи запити до модуля:

```javascript
// Routed through the core's module API proxy
const res = await fetch('/api/ui/modules/{module-name}/api/endpoint');
const data = await res.json();
```

Альтернативно, реалізуйте метод `handle_api_request()` у вашому підкласі `SmartHomeModule` для програмної обробки вхідних API-запитів.

### Типізований postMessage-протокол (custom-віджети ↔ панель)

Custom-iframe-віджети спілкуються з chrome панелі через фіксований message-контракт. Phase 5 видалила legacy-аліаси; єдині прийнятні форми:

```ts
type WidgetMessage =
    | { type: "ready" }                                                          // iframe → on load
    | { type: "modal_open"; module: string; width?: number; height?: number }    // expand fullscreen
    | { type: "modal_close"; module: string }                                    // collapse modal
    | { type: "modal_resize"; width: number; height: number }                    // resize hint
    | { type: "open_settings"; module: string }                                  // navigate to settings
    | { type: "request_refresh" }                                                // ask to refetch
    | { type: "theme_changed"; theme: "dark" | "light" };                        // core → on theme switch
```

```javascript
// iframe → parent: відкрити віджет у fullscreen-modal
window.parent.postMessage({type: 'modal_open', module: 'lights-switches', width: 480, height: 560}, '*');

// iframe → parent: закрити modal
window.parent.postMessage({type: 'modal_close', module: 'lights-switches'}, '*');

// iframe → parent: відкрити settings-сторінку модуля
window.parent.postMessage({type: 'open_settings', module: 'lights-switches'}, '*');
```

Видалено в Phase 5: `openWidgetModal`, `closeWidgetModal`, `openSettings`, `refresh` — pre-Phase-4 аліаси. Вони більше не доходять до handler'а панелі. Канонічні імена вище — єдиний прийнятний формат. Дивись [`src/lib/widgetMessages.ts`](../../src/lib/widgetMessages.ts) для runtime-нормалізатора.

### Оновлення в реальному часі

Для віджетів, яким потрібні живі дані (показники датчиків, стани пристроїв), використовуйте періодичне опитування за допомогою `setInterval`. Оберіть інтервал, який балансує між актуальністю та використанням ресурсів:

```javascript
// Poll every 30 seconds for sensor data
setInterval(async () => {
    const res = await fetch('/api/ui/modules/sensors/latest');
    const data = await res.json();
    updateDisplay(data);
}, 30000);
```

Рекомендовані інтервали опитування:

| Тип даних              | Інтервал     |
|------------------------|--------------|
| Температура, вологість | 30-60 сек    |
| Стан пристрою          | 10-30 сек    |
| Лічильники, статистика | 60-300 сек   |
| Критичні сповіщення    | Використовуйте SSE |

Уникайте інтервалів коротших за 5 секунд, якщо дані дійсно не змінюються так часто.

---

## Вимоги до іконок

Іконки модулів відображаються у бічній панелі та на плитках віджетів.

| Вимога          | Значення                                         |
|-----------------|--------------------------------------------------|
| Формат          | SVG                                              |
| viewBox         | `0 0 24 24` (рекомендовано)                      |
| Колір           | Використовуйте `currentColor` для сумісності з темами |
| Розташування    | Кореневий каталог модуля                         |
| Назва файлу     | Оголошується у `manifest.json` в полі `ui.icon`  |

### Приклад іконки

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
     stroke="currentColor" stroke-width="2" stroke-linecap="round"
     stroke-linejoin="round">
    <path d="M12 2v2"/>
    <circle cx="12" cy="12" r="5"/>
    <path d="M12 20v2"/>
    <path d="M4.93 4.93l1.41 1.41"/>
    <path d="M17.66 17.66l1.41 1.41"/>
    <path d="M2 12h2"/>
    <path d="M20 12h2"/>
    <path d="M4.93 19.07l1.41-1.41"/>
    <path d="M17.66 6.34l1.41-1.41"/>
</svg>
```

Використання `currentColor` означає, що іконка автоматично відповідає кольору навколишнього тексту, адаптуючись до світлої та темної тем без додаткових зусиль.

---

## Повний приклад UI модуля

Нижче наведена повна структура файлів для модуля з UI-профілем `FULL`:

```
modules/
  my-sensor-module/
    manifest.json
    icon.svg
    widget.html
    settings.html
    module.py
```

**manifest.json:**

```json
{
    "name": "my-sensor-module",
    "version": "1.0.0",
    "description": "Temperature and humidity sensor display",
    "ui_profile": "FULL",
    "ui": {
        "icon": "icon.svg",
        "widget": {
            "file": "widget.html",
            "size": "2x1",
            "max_size": "2x2"
        },
        "settings": "settings.html"
    }
}
```

**widget.html:**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: transparent;
            color: var(--text-color, #333);
            padding: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            height: 100vh;
        }
        .reading { text-align: center; flex: 1; }
        .reading .value { font-size: 28px; font-weight: 700; }
        .reading .label {
            font-size: 11px;
            color: var(--text-secondary, #666);
            margin-top: 4px;
        }
        .divider {
            width: 1px;
            height: 40px;
            background: var(--border-color, #e0e0e0);
            margin: 0 8px;
        }
        .error {
            text-align: center;
            width: 100%;
            color: var(--text-secondary, #999);
            font-size: 13px;
        }
    </style>
</head>
<body>
    <div id="content">
        <span class="error">Loading...</span>
    </div>

    <script>
        async function refresh() {
            try {
                const res = await fetch('/api/ui/modules/my-sensor-module/readings');
                if (!res.ok) throw new Error(res.statusText);
                const data = await res.json();

                document.getElementById('content').innerHTML = `
                    <div class="reading">
                        <div class="value">${data.temperature.toFixed(1)}°C</div>
                        <div class="label">Temperature</div>
                    </div>
                    <div class="divider"></div>
                    <div class="reading">
                        <div class="value">${data.humidity.toFixed(0)}%</div>
                        <div class="label">Humidity</div>
                    </div>
                `;
            } catch (e) {
                document.getElementById('content').innerHTML =
                    '<span class="error">Sensor unavailable</span>';
            }
        }
        refresh();
        setInterval(refresh, 30000);
    </script>
</body>
</html>
```

---

## Найкращі практики

1. **Тримайте віджети легкими.** Мінімізуйте JavaScript та CSS. Уникайте важких фреймворків всередині iframe віджетів.
2. **Використовуйте CSS-змінні для тем оформлення.** Це забезпечує візуальну узгодженість по всій панелі у світлому та темному режимах.
3. **Обробляйте помилки коректно.** Завжди показуйте запасний вміст (тире, "Недоступно" або пропозицію повторити) замість того, щоб залишати віджет порожнім або показувати стек помилок.
4. **Встановіть `background: transparent`.** Плитка панелі забезпечує фон картки. Віджет з власним непрозорим фоном виглядатиме недоречно.
5. **Оновлюйте дані з розумними інтервалами.** Кожні 30-60 секунд підходить для більшості даних датчиків. Не опитуйте частіше ніж кожні 5 секунд.
6. **Робіть віджети адаптивними.** Віджет має коректно виглядати при розмірі за замовчуванням `size` та при кожному розмірі до `max_size`. Використовуйте відносні одиниці та flexbox.
7. **Використовуйте SVG для іконок.** SVG чітко масштабується при будь-якій роздільній здатності та підтримує `currentColor` для автоматичної адаптації до теми.
8. **Завантажуйте налаштування при відкритті сторінки.** Сторінка налаштувань, яка відкривається з порожніми полями, змушує користувача повторно вводити вже налаштовані значення.
9. **Валідуйте перед збереженням.** Перевіряйте обов'язкові поля та діапазони значень на стороні клієнта перед відправкою POST-запиту.
10. **Показуйте зворотний зв'язок при збереженні.** Завжди підтверджуйте успіх або повідомляйте про помилку після натискання кнопки "Зберегти".
