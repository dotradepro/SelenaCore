# docs/uk/widget-development.md — Розробка віджетів та налаштувань

**Версія:** 1.0
**Статус:** Нормативний документ
**Область:** `widget.html`, `settings.html` в кожному модулі

---

## 1. Сітка дашборда (розмір комірок)

Дашборд UI Core використовує CSS Grid. Кожен модуль з `ui_profile != HEADLESS` отримує одну або кілька комірок.

### Розміри комірок (`manifest.ui.widget.size`)

| Розмір | Колонки × Рядки | Призначення |
|--------|-----------------|-------------|
| `1x1`  | 1 × 1           | Мінімальний: одне значення / іконка / перемикач |
| `2x1`  | 2 × 1           | Стандартний: назва + значення + графік |
| `2x2`  | 2 × 2           | Розширений: карта / камера / список |
| `4x1`  | 4 × 1           | Панель: повна ширина, один рядок |
| `1x2`  | 1 × 2           | Вертикальний: список / timeline |

### Реальні розміри iframe (залежать від екрана)

| Екран | Колонки сітки | Розмір комірки ≈ |
|-------|---------------|------------------|
| 1920 × 1080 (Chromium kiosk) | 4 | 460 × 240 px |
| 1280 × 720 (планшет)        | 4 | 300 × 200 px |
| 768 × 1024 (iPad portrait)   | 2 | 360 × 240 px |
| 375 × 812 (iPhone portrait)  | 1 | 355 × 200 px |

> **Правило:** віджет повинен коректно виглядати при ширині від **300px** до **500px** та висоті від **140px** до **500px**. Не hardcode конкретних пікселів — використовуй `%`, `flex`, `fr`.

---

## 2. iframe sandbox — правила ізоляції

```html
<iframe
  src="/api/ui/modules/{module_name}/widget.html"
  sandbox="allow-scripts allow-same-origin"
  scrolling="no"
  style="width: 100%; height: 100%; border: none;">
</iframe>
```

**Що дозволено:**
- `allow-scripts` — JavaScript виконується
- `allow-same-origin` — `fetch()` до API (same-origin) працює

**Що заборонено (sandbox блокує):**
- Навігація top-level (модуль не може підмінити UI Core)
- Форми з `target="_blank"`
- Попапи (`window.open`)
- Завантаження плагінів

**Наслідки для розробника:**
- Зовнішні бібліотеки можна підключати тільки через `<script>` тег (не через `import`)
- CSP дозволяє `script-src 'self' 'unsafe-inline'` для inline JS
- Зображення: тільки локальні або data-URI (зовнішні URL блокуються CSP)

---

## 3. Обов'язковий CSS-шаблон (widget.html)

Кожен `widget.html` **зобов'язаний** починатися з цього CSS-ресету, щоб не ламати сітку:

```css
*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html, body {
  width: 100%;
  height: 100%;
  overflow: hidden;       /* ← НІЯКОГО скролу у віджеті */
  background: #0e1220;
  color: #e0e4f0;
  font-family: 'Segoe UI', system-ui, sans-serif;
}
```

### ⛔ Заборонені CSS-патерни

```css
/* ❌ Виставляють фіксовану висоту → порушує адаптивність */
html, body { height: 100vh; }
html, body { min-height: 100vh; }
.root { height: 400px; }

/* ❌ Фіксовані позиції → вилазять за межі iframe */
.element { position: fixed; top: 0; }

/* ❌ Overflow auto/scroll → з'являється полоса прокрутки */
body { overflow: auto; }
body { overflow-y: scroll; }
```

### ✅ Правильний root-контейнер

```css
.root {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  padding: 14px 16px;
  gap: 10px;
  overflow: hidden;
}
```

---

## 4. BASE URL — єдиний правильний спосіб

```javascript
// ✅ ПРАВИЛЬНО — обчислюється з URL iframe
const BASE = window.location.pathname
  .replace(/\/(widget|settings)(\.html)?(\?.*)?$/, '');

// Приклад:
// URL iframe: /api/ui/modules/weather-service/widget.html
// BASE =      /api/ui/modules/weather-service

// Усі запити — тільки через BASE:
fetch(BASE + '/weather/current')
fetch(BASE + '/config')
fetch(BASE + '/status')
```

```javascript
// ❌ НЕПРАВИЛЬНО — зламається при зміні хосту/порту
const BASE = "http://localhost:8115";    // хардкод порту
const base = window.location.origin;     // не враховує prefix
fetch('/status');                         // без prefix → 404
```

---

## 5. Адаптація під розмір (compact mode)

При `height < 160px` (маленька комірка або мобільний) — віджет повинен перейти в компактний режим, ховаючи другорядний контент.

```javascript
function checkLayout() {
  const root = document.getElementById('root');
  if (!root) return;
  root.classList.toggle('compact', root.offsetHeight < 160);
}

window.addEventListener('resize', checkLayout);
checkLayout(); // при ініціалізації
```

```css
/* Компактний режим */
.compact .secondary { display: none; }
.compact .chart     { display: none; }
.compact .title     { font-size: 0.8rem; }
```

**Правило:** у компактному режимі повинні залишатися **тільки**: назва + головне значення + іконка стану.

---

## 6. Завантаження даних (fetch)

### Шаблон

```javascript
async function load() {
  try {
    const resp = await fetch(BASE + '/status');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    render(data);
  } catch (e) {
    renderError(e.message);
  }
}
```

### Автооновлення

```javascript
// Poll кожні 30 секунд
setInterval(load, 30_000);

// АБО SSE (Server-Sent Events) — якщо модуль підтримує
const sse = new EventSource(BASE + '/stream');
sse.onmessage = (e) => {
  const data = JSON.parse(e.data);
  render(data);
};
sse.onerror = () => {
  renderError('SSE connection lost');
};
```

### Обробка помилок

```javascript
function renderError(message) {
  document.getElementById('root').innerHTML = `
    <div class="state">
      <div class="icon">⚠️</div>
      <div>${message}</div>
    </div>`;
}
```

---

## 7. Запити до Core API (з віджета)

Для запитів до Core API (`/api/v1/*`) з віджета потрібен `ui_token`:

```javascript
// ui_token передається через query parameter при завантаженні iframe
const uiToken = new URLSearchParams(window.location.search).get('ui_token');

// Використання:
const resp = await fetch('/api/v1/devices', {
  headers: { 'Authorization': `Bearer ${uiToken}` }
});
```

**Обмеження ui_token:**
- Тільки `device.read`, `events.subscribe` (read-only)
- TTL = 1 година
- Не є module_token

---

## 8. SSE та postMessage

### SSE від модуля

```javascript
const sse = new EventSource(BASE + '/stream');
sse.addEventListener('status', (e) => {
  const data = JSON.parse(e.data);
  updateWidget(data);
});
```

### postMessage (iframe ↔ UI Core)

UI Core може надсилати повідомлення у iframe:

```javascript
// UI Core → iframe:
iframe.contentWindow.postMessage({ type: 'theme_changed', theme: 'dark' }, '*');
iframe.contentWindow.postMessage({ type: 'lang_changed' }, '*');

// widget.html — прийом:
window.addEventListener('message', (e) => {
  if (e.data.type === 'theme_changed') {
    document.body.className = e.data.theme;
  }
  if (e.data.type === 'lang_changed') {
    try { LANG = localStorage.getItem('selena-lang') || 'en'; } catch (ex) {}
    applyLang();
    load(); // перезавантажити дані
  }
});
```

---

## 9. settings.html — правила

### Відмінності від widget.html

| Властивість | widget.html | settings.html |
|-------------|-------------|---------------|
| `overflow` | `hidden` | `auto` (скрол дозволений) |
| `height` | `100%` (фіксований iframe) | `min-height: 100%` |
| Скрол | Заборонений | Дозволений |
| Адаптивність | Compact mode | Немає (модалка) |
| Збереження | Тільки читання | POST /config |

### CSS ресет для settings.html

```css
html, body {
  width: 100%;
  min-height: 100%;        /* ← не 100vh */
  overflow-y: auto;         /* ← скрол дозволений */
  background: #0e1220;
  color: #e0e4f0;
}
```

### Збереження налаштувань

```javascript
async function save() {
  const body = {
    param1: document.getElementById('param1').value,
    param2: document.getElementById('param2').value,
  };
  try {
    const r = await fetch(BASE + '/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    showMsg('Saved!', 'ok');
  } catch (e) {
    showMsg('Error: ' + e.message, 'err');
  }
}
```

---

## 10. Повний шаблон widget.html

Готовий мінімальний шаблон для копіювання:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>My Widget</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    /* Обов'язково: заповнити iframe без overflow */
    html, body {
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: #0e1220;
      color: #e0e4f0;
      font-family: 'Segoe UI', system-ui, sans-serif;
    }

    .root {
      display: flex;
      flex-direction: column;
      width: 100%;
      height: 100%;
      padding: 14px 16px;
      gap: 10px;
    }

    /* ── Стани завантаження / помилки ── */
    .state {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      width: 100%;
      height: 100%;
      gap: 8px;
      color: #3a4060;
      font-size: 0.82rem;
      text-align: center;
    }
    .state .icon { font-size: 1.8rem; opacity: .4; }

    @keyframes pulse { 0%,100%{opacity:.3} 50%{opacity:.9} }
    .pulse { animation: pulse 1.8s ease-in-out infinite; }

    /* ── Compact mode (height < 160px) ── */
    .compact .secondary { display: none; }

    /* ── Ваші стилі ── */

  </style>
</head>
<body>

<div class="root" id="root">
  <div class="state">
    <div class="icon pulse">⏳</div>
    <div>Loading…</div>
  </div>
</div>

<script>
(function () {
  // ── BASE URL — єдиний правильний спосіб ──────────────────────────
  const BASE = window.location.pathname
    .replace(/\/(widget|settings)(\.html)?(\?.*)?$/, '');

  // ── ui_token для запитів до Core API ──────────────────────────────────
  const uiToken = new URLSearchParams(window.location.search).get('ui_token');

  // ── Адаптація під розмір комірки ────────────────────────────────────────
  function checkLayout() {
    const root = document.getElementById('root');
    if (!root) return;
    root.classList.toggle('compact', root.offsetHeight < 160);
  }

  // ── Рендер (ваш код) ──────────────────────────────────────────────────
  function render(data) {
    const root = document.getElementById('root');
    root.innerHTML = `
      <div>Дані: ${JSON.stringify(data)}</div>
    `;
    checkLayout();
  }

  // ── Завантаження даних ────────────────────────────────────────────────────
  async function load() {
    try {
      const data = await fetch(BASE + '/status').then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      });
      render(data);
    } catch (e) {
      document.getElementById('root').innerHTML = `
        <div class="state">
          <div class="icon">⚠️</div>
          <div>${e.message}</div>
        </div>`;
    }
  }

  // ── Ініціалізація ──────────────────────────────────────────────────
  checkLayout();
  load();
  setInterval(load, 30_000);
  window.addEventListener('resize', checkLayout);
})();
</script>
</body>
</html>
```

---

## 11. Повний шаблон settings.html

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Settings</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    /* settings — прокручуване модальне вікно, не фіксована комірка */
    html, body {
      width: 100%;
      min-height: 100%;     /* ← не 100vh */
      overflow-y: auto;     /* ← скрол дозволений */
      background: #0e1220;
      color: #e0e4f0;
      font-family: 'Segoe UI', system-ui, sans-serif;
    }

    .settings-root {
      padding: 20px 16px 32px;
      max-width: 560px;
    }

    h1 { font-size: 1.2rem; margin-bottom: 20px; color: #fff; }

    .section {
      background: rgba(255,255,255,.05);
      border: 1px solid rgba(255,255,255,.07);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 14px;
    }

    .section h2 {
      font-size: 0.8rem;
      color: #5a6080;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 12px;
    }

    label {
      display: block;
      font-size: 0.8rem;
      color: #7880a0;
      margin-bottom: 4px;
    }

    input, select {
      width: 100%;
      background: rgba(0,0,0,.4);
      border: 1px solid rgba(255,255,255,.1);
      border-radius: 8px;
      color: #e0e4f0;
      padding: 9px 12px;
      font-size: 0.88rem;
      margin-bottom: 12px;
    }

    input:focus, select:focus {
      outline: none;
      border-color: #4a6ee0;
    }

    .btn {
      display: inline-block;
      background: #3a5cc8;
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 10px 18px;
      font-size: 0.9rem;
      cursor: pointer;
    }
    .btn:hover  { background: #2e4eb0; }
    .btn-ghost  { background: rgba(255,255,255,.07); }
    .btn-ghost:hover { background: rgba(255,255,255,.12); }
    .btn-full   { width: 100%; text-align: center; }

    .msg {
      margin-top: 10px;
      padding: 8px 12px;
      border-radius: 8px;
      font-size: 0.82rem;
    }
    .msg.ok  { background: rgba(50,200,100,.12); color: #3cc870; }
    .msg.err { background: rgba(220,50,50,.12); color: #e05858; }
  </style>
</head>
<body>
<div class="settings-root">
  <h1>⚙️ Налаштування модуля</h1>

  <div class="section">
    <h2>Конфігурація</h2>

    <label>Параметр 1</label>
    <input type="text" id="param1" placeholder="Значення">

    <label>Параметр 2</label>
    <select id="param2">
      <option value="a">Варіант A</option>
      <option value="b">Варіант B</option>
    </select>
  </div>

  <button class="btn btn-full" onclick="save()">💾 Зберегти</button>
  <button class="btn btn-ghost btn-full" style="margin-top:8px" onclick="loadStatus()">🔄 Оновити</button>

  <div id="msg"></div>
</div>

<script>
  const BASE = window.location.pathname
    .replace(/\/(widget|settings)(\.html)?(\?.*)?$/, '');

  // ── Завантажити поточні налаштування ────────────────────────────────────────
  async function loadStatus() {
    try {
      const s = await fetch(BASE + '/status').then(r => r.json());
      if (s.param1) document.getElementById('param1').value = s.param1;
      if (s.param2) document.getElementById('param2').value = s.param2;
    } catch (e) {
      showMsg('Помилка завантаження: ' + e.message, 'err');
    }
  }

  // ── Зберегти ──────────────────────────────────────────────────────────
  async function save() {
    const body = {
      param1: document.getElementById('param1').value,
      param2: document.getElementById('param2').value,
    };
    try {
      const r = await fetch(BASE + '/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      showMsg('Збережено!', 'ok');
    } catch (e) {
      showMsg('Помилка: ' + e.message, 'err');
    }
  }

  function showMsg(text, type) {
    const el = document.getElementById('msg');
    el.className = 'msg ' + type;
    el.textContent = text;
    setTimeout(() => { el.textContent = ''; el.className = ''; }, 5000);
  }

  loadStatus();
</script>
</body>
</html>
```

---

## 12. Чеклист перед здачею

**CSS:**
- [ ] `html, body { width: 100%; height: 100%; overflow: hidden; }` — у widget.html
- [ ] Немає `100vh`, `min-height: 100vh` у widget.html
- [ ] `.root { width: 100%; height: 100%; display: flex; overflow: hidden; }`
- [ ] Немає `position: fixed` елементів у widget.html
- [ ] settings.html використовує `min-height: 100%` та `overflow-y: auto`

**JavaScript:**
- [ ] `BASE` обчислюється через `window.location.pathname.replace(...)`
- [ ] Немає хардкоду `localhost:PORT` або IP-адрес
- [ ] Немає `fetch('/endpoint')` без BASE префіксу
- [ ] Є обробка помилок fetch (try/catch + показ стану помилки)
- [ ] Є автооновлення даних через `setInterval` (або SSE)
- [ ] Є `window.addEventListener('resize', checkLayout)` для адаптації

**Функціональність:**
- [ ] Віджет показує стан завантаження (`Loading...`) поки немає даних
- [ ] Віджет показує стан помилки якщо API недоступний
- [ ] При порожніх даних (`null`, `undefined`) немає падіння — показується `'—'`
- [ ] Компактний режим при `height < 160px` ховає другорядний контент

**Відповідність розміру:**
- [ ] Контент поміщається в комірку без скролу при базовому розмірі
- [ ] При зменшенні iframe (compact) нічого не обрізається некрасиво

---

## 13. Типові помилки

### ❌ Біла смуга знизу / контент не заповнює комірку

**Причина:** `body` має стандартний відступ або `height` не задано.

```css
/* Виправлення */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 100%; height: 100%; }
```

---

### ❌ Смуга прокрутки з'являється в комірці дашборда

**Причина:** контент виходить за `height: 100%`, а `overflow: hidden` не задано.

```css
/* Виправлення */
html, body { overflow: hidden; }
.root      { overflow: hidden; }
```

---

### ❌ Прогноз / список обрізається зверху або знизу

**Причина:** flex-дочірній елемент не може стиснутися менше свого вмісту.

```css
/* Виправлення */
.forecast {
  flex: 1;
  min-height: 0;  /* ← ключова властивість, дозволяє flex-item стискатися */
  overflow: hidden;
}
```

---

### ❌ Запити до API падають з CORS або 404

**Причина:** неправильно обчислений BASE URL (хардкод порту або origin без шляху).

```javascript
// Виправлення
const BASE = window.location.pathname
  .replace(/\/(widget|settings)(\.html)?(\?.*)?$/, '');
```

---

### ❌ Модуль показує старі дані після зміни налаштувань

**Причина:** немає перезавантаження даних після збереження в settings.

```javascript
// В settings.html — після успішного save():
async function save() {
  // ... POST /config ...
  showMsg('Збережено!', 'ok');
  // Перезавантажити статус щоб відобразити зміни
  await loadStatus();
}
```

---

### ❌ TypeError: Cannot read properties of null / undefined

**Причина:** дані прийшли, але поле відсутнє — немає захисту від null.

```javascript
// ❌ Падає якщо temperature == null
document.getElementById('temp').textContent = Math.round(data.temperature) + '°';

// ✅ Безпечно
const t = data.temperature;
document.getElementById('temp').textContent =
  t != null ? Math.round(t) + '°' : '—';
```

---

*SelenaCore · Розробка віджетів · UK переклад · MIT*
