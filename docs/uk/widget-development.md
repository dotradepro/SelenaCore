# Посібник з розробки віджетів

Цей посібник описує створення UI-віджетів, сторінок налаштувань та іконок для модулів SelenaCore. Модулі можуть надавати віджети для панелі керування та інтерфейси конфігурації, які обслуговуються ядром за адресою `/api/ui/modules/{module_name}/`.

---

## Зміст

1. [UI-профілі](#ui-профілі)
2. [Секція UI у manifest.json](#секція-ui-у-manifestjson)
3. [Розміри сітки](#розміри-сітки)
4. [HTML-структура віджета](#html-структура-віджета)
5. [Сторінка налаштувань](#сторінка-налаштувань)
6. [Шаблони комунікації](#шаблони-комунікації)
7. [Теми оформлення](#теми-оформлення)
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

Додайте блок `ui` до `manifest.json` вашого модуля, щоб оголосити всі UI-ресурси:

```json
{
    "ui": {
        "icon": "icon.svg",
        "widget": {
            "file": "widget.html",
            "size": "2x2",
            "max_size": "4x4"
        },
        "settings": "settings.html"
    }
}
```

### Опис полів

| Поле              | Тип    | Опис                                                 |
|-------------------|--------|------------------------------------------------------|
| `ui.icon`         | string | Шлях до SVG-файлу іконки (відносно кореня модуля)    |
| `ui.widget.file`  | string | HTML-файл для віджета панелі керування               |
| `ui.widget.size`  | string | Розмір сітки за замовчуванням (`"ШxВ"`, напр. `"2x2"`) |
| `ui.widget.max_size` | string | Максимальний розмір сітки, до якого користувач може змінити розмір |
| `ui.settings`     | string | HTML-файл для сторінки налаштувань модуля             |

Усі шляхи до файлів є відносними до кореневого каталогу модуля.

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

## HTML-структура віджета

Віджети вбудовуються як iframe на панелі керування. Кожен віджет має бути самостійним HTML-файлом із вбудованими стилями та скриптами.

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

## Теми оформлення

Панель керування вставляє CSS-змінні (custom properties) в iframe віджетів. Використовуйте ці змінні, щоб ваш віджет адаптувався до світлої або темної теми користувача:

```css
body {
    background: transparent;
    color: var(--text-color, #333);
}

.secondary-text {
    color: var(--text-secondary, #666);
}

.card {
    background: var(--surface-color, #ffffff);
    border: 1px solid var(--border-color, #e0e0e0);
    border-radius: 8px;
}

.accent {
    color: var(--accent-color, #007AFF);
}
```

Завжди вказуйте запасне значення (другий аргумент `var()`), щоб віджет коректно відображався, навіть якщо змінні теми ще не вставлені.

### Підтримка темного режиму

```css
/* Fallback dark mode if CSS variables are not provided */
@media (prefers-color-scheme: dark) {
    body {
        color: var(--text-color, #e0e0e0);
    }
    .secondary-text {
        color: var(--text-secondary, #999);
    }
}
```

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
