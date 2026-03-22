# Widget & Settings UI Development Guide
**SelenaCore · UI Module Reference**

Этот документ описывает как правильно писать `widget.html` и `settings.html` для модулей SelenaCore. Прочитай **целиком** перед тем как верстать любой UI модуля.

---

## Оглавление

1. [Как работает сетка дашборда](#1-как-работает-сетка-дашборда)
2. [Ключевое правило: виджет живёт в iframe](#2-ключевое-правило-виджет-живёт-в-iframe)
3. [Обязательный CSS-шаблон](#3-обязательный-css-шаблон)
4. [BASE URL — единственный правильный способ](#4-base-url--единственный-правильный-способ)
5. [Размеры ячеек и адаптация](#5-размеры-ячеек-и-адаптация)
6. [Получение данных из модуля](#6-получение-данных-из-модуля)
7. [Запросы к Core API из виджета](#7-запросы-к-core-api-из-виджета)
8. [Realtime: SSE и postMessage](#8-realtime-sse-и-postmessage)
9. [settings.html — правила](#9-settingshtml--правила)
10. [Полный шаблон widget.html](#10-полный-шаблон-widgethtml)
11. [Полный шаблон settings.html](#11-полный-шаблон-settingshtml)
12. [Чеклист перед сдачей](#12-чеклист-перед-сдачей)
13. [Типичные ошибки](#13-типичные-ошибки)

---

## 1. Как работает сетка дашборда

UI Core (:80) строит дашборд как CSS grid. Каждый модуль со статусом `RUNNING` и `ui_profile != HEADLESS` получает ячейку в этой сетке. Размер ячейки задаётся в `manifest.json`:

```json
"ui": {
  "widget": {
    "file": "widget.html",
    "size": "2x1"
  }
}
```

| Значение `size` | Ширина | Высота | Типичное использование |
|---|---|---|---|
| `1x1` | 1 колонка | 1 строка | Простой индикатор, счётчик |
| `2x1` | 2 колонки | 1 строка | Компактный статус с деталями |
| `1x2` | 1 колонка | 2 строки | Узкий вертикальный список |
| `2x2` | 2 колонки | 2 строки | Полноценный виджет с графиком/прогнозом |
| `4x1` | вся ширина | 1 строка | Горизонтальная панель |

UI Core создаёт для каждого модуля:

```html
<iframe
  src="http://localhost:{port}/widget.html?ui_token=..."
  sandbox="allow-scripts allow-same-origin"
  scrolling="no"
  style="width: {N*cell_px}px; height: {M*row_px}px; border: none;"
/>
```

Конкретные пиксельные размеры зависят от конфигурации UI Core (разрешение экрана, количество колонок). **Виджет не знает и не должен знать точные пиксели** — он просто заполняет 100% выделенного iframe.

---

## 2. Ключевое правило: виджет живёт в iframe

iframe — это изолированный документ фиксированного размера. Внутри него:

- **нет `100vh`** — `vh` считается от высоты самого iframe, который уже имеет фиксированную высоту. Это приводит к переполнению.
- **нет скролла** — `scrolling="no"` задан родителем. Контент, который не влез — обрезается.
- **нет доступа к parent DOM** — `sandbox` запрещает `window.parent`, `window.top`, `document.cookie`.
- **нет `alert()`, `confirm()`, `prompt()`** — заблокированы sandbox.
- **нет localStorage/sessionStorage** — `allow-same-origin` есть, но полагаться на storage между перезагрузками не стоит; данные должны приходить из API модуля.

```
┌─────────────────────────────────┐
│  UI Core dashboard (браузер)    │
│                                  │
│  ┌──────────┐  ┌──────────────┐ │
│  │ iframe   │  │ iframe 2x2   │ │
│  │ 1x1      │  │              │ │
│  │ widget   │  │  widget.html │ │
│  └──────────┘  │  занимает    │ │
│                │  100%x100%   │ │
│  ┌─────────────┤  ячейки      │ │
│  │ iframe 4x1  └──────────────┘ │
│  └─────────────────────────────┘│
└─────────────────────────────────┘
```

---

## 3. Обязательный CSS-шаблон

Это единственный правильный способ начать `widget.html`. Отклонение от него — причина #1 багов отображения.

```css
/* ── Сброс — виджет в iframe заполняет ячейку сетки ── */
*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

/* html и body — ровно 100% iframe */
html, body {
  width: 100%;
  height: 100%;
  overflow: hidden;       /* ← обязательно, scrolling="no" у родителя */
  background: transparent; /* или свой цвет */
}

/* Корневой контейнер — заполняет всё */
.root {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  /* padding по вкусу */
  padding: 14px 16px;
  gap: 10px;
  overflow: hidden;       /* ← контент не выходит за рамки */
}
```

**Запрещено:**

```css
/* ❌ ломает отображение */
body { min-height: 100vh; }
body { height: 100vh; }
.container { min-height: 100vh; }

/* ❌ вызывает скролл внутри iframe */
body { overflow: auto; }
body { overflow-y: scroll; }

/* ❌ контент выходит за рамки ячейки */
.widget { position: fixed; }
.popup  { position: fixed; }
```

---

## 4. BASE URL — единственный правильный способ

Виджет и настройки загружаются по пути вида:

```
# Пользовательский модуль (Docker контейнер)
http://localhost:8115/widget.html?ui_token=...

# Системный модуль (in-process, монтируется в core FastAPI)
http://localhost:7070/api/ui/modules/weather-service/widget.html?ui_token=...
```

Путь к API модуля всегда совпадает с директорией, где лежит виджет. Получить его надо из текущего URL:

```javascript
// ✅ Правильно — работает для обоих типов модулей
const BASE = window.location.pathname
  .replace(/\/(widget|settings)(\.html)?(\?.*)?$/, '');

// Примеры:
// /widget.html                           → ''
// /api/ui/modules/weather-service/widget → '/api/ui/modules/weather-service'

// Использование:
fetch(BASE + '/weather/current')
fetch(BASE + '/status')
```

```javascript
// ❌ Неправильно — хардкод порта
const BASE = 'http://localhost:8115';

// ❌ Неправильно — не учитывает префикс пути системного модуля
const BASE = window.location.origin;

// ❌ Неправильно — относительный путь без BASE
fetch('/weather/current');
```

---

## 5. Размеры ячеек и адаптация

Виджет не знает свой точный размер в пикселях, но может его получить в runtime и адаптироваться:

```javascript
// Определить размер при старте и при изменении (родитель может изменить iframe)
function checkLayout() {
  const root = document.getElementById('root');
  const w = root.offsetWidth;
  const h = root.offsetHeight;

  // Компактный режим для маленьких ячеек (1x1, 2x1)
  root.classList.toggle('compact', h < 160);

  // Широкий режим для 4x1
  root.classList.toggle('wide', w > 600 && h < 160);
}

checkLayout();
window.addEventListener('resize', checkLayout);
```

**Рекомендуемые пороги:**

| Условие | Класс | Поведение |
|---|---|---|
| `height < 160px` | `.compact` | Скрыть второстепенные детали, уменьшить шрифт |
| `height > 300px` | `.expanded` | Показать расширенный контент, графики |
| `width > 500px` | `.wide` | Горизонтальный layout вместо вертикального |

**Пример CSS адаптации:**

```css
/* Базовый вид (2x1, 2x2) */
.detail-text   { display: block; }
.main-temp     { font-size: 2.2rem; }
.forecast-cond { display: block; }

/* Компактный (1x1, узкие ячейки) */
.compact .detail-text   { display: none; }
.compact .main-temp     { font-size: 1.6rem; }
.compact .forecast-cond { display: none; }

/* Широкий (4x1) */
.wide .layout   { flex-direction: row; }
.wide .forecast { grid-template-columns: repeat(5, 1fr); }
```

---

## 6. Получение данных из модуля

Виджет получает данные **только** от своего модуля через HTTP-запросы к его API:

```javascript
const BASE = window.location.pathname.replace(/\/(widget|settings)(\.html)?$/, '');

async function load() {
  try {
    const data = await fetch(BASE + '/status').then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
    render(data);
  } catch (e) {
    showError(e.message);
  }
}

// Первая загрузка
load();

// Автообновление (выбрать подходящий интервал)
setInterval(load, 30_000); // каждые 30 секунд
```

**Рекомендации по интервалу опроса:**

| Тип данных | Интервал |
|---|---|
| Температура, влажность | 30–60 сек |
| Статус устройства | 10–30 сек |
| Счётчики, статистика | 60–300 сек |
| Критические алерты | SSE (см. раздел 8) |

---

## 7. Запросы к Core API из виджета

Если виджету нужны данные напрямую из Core API (список устройств и т.д.), UI Core передаёт `ui_token` через query параметр:

```javascript
// Получить ui_token из URL
const uiToken = new URLSearchParams(window.location.search).get('ui_token');

// Запрос к Core API
const devices = await fetch('http://localhost:7070/api/v1/devices', {
  headers: { 'Authorization': `Bearer ${uiToken}` }
}).then(r => r.json());
```

**Ограничения `ui_token`:**
- Только чтение: `device.read`, `events.subscribe`
- TTL: 1 час (виджет сам обновит страницу при 401)
- Нельзя писать устройства, публиковать события, управлять модулями

```javascript
// Обработка истёкшего токена
async function coreRequest(url) {
  const res = await fetch(url, {
    headers: { 'Authorization': `Bearer ${uiToken}` }
  });
  if (res.status === 401) {
    // Токен истёк — тихо перезагрузить iframe
    window.location.reload();
    return null;
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

---

## 8. Realtime: SSE и postMessage

### SSE от модуля (рекомендуется для realtime данных)

```javascript
const BASE = window.location.pathname.replace(/\/(widget|settings)(\.html)?$/, '');

const es = new EventSource(BASE + '/events/stream');

es.addEventListener('state_changed', (e) => {
  const data = JSON.parse(e.data);
  updateUI(data);
});

es.onerror = () => {
  // Переподключение — EventSource делает это автоматически
  console.warn('SSE reconnecting...');
};
```

### postMessage с parent (только UI Core → виджет)

Sandbox разрешает `allow-scripts allow-same-origin` — `postMessage` работает в одну сторону: от parent к iframe. Виджет может слушать сообщения от UI Core:

```javascript
window.addEventListener('message', (e) => {
  // Проверить источник — только от того же origin
  if (e.origin !== window.location.origin) return;

  if (e.data?.type === 'theme_changed') {
    applyTheme(e.data.theme);
  }
  if (e.data?.type === 'refresh') {
    load();
  }
});
```

**Нельзя:** отправлять сообщения из iframe в parent (заблокировано sandbox без `allow-same-origin` + явного `targetOrigin`).

---

## 9. settings.html — правила

Страница настроек открывается в **отдельном модальном окне** UI Core, не в сетке дашборда. Правила:

- Размер **не фиксирован** — страница показывается в прокручиваемом модальном окне.
- `overflow: auto` на body **разрешён** — здесь скролл нужен.
- `100vh` **запрещён** по той же причине — используй `min-height: 100%`.
- Формы сохраняют данные **через API модуля**, не через `localStorage`.

```css
/* settings.html — body */
html, body {
  width: 100%;
  min-height: 100%;  /* ← не 100vh */
  overflow-y: auto;  /* ← здесь скролл разрешён */
  background: #0e1220;
  color: #e8eaf0;
  font-family: 'Segoe UI', system-ui, sans-serif;
}

.settings-root {
  padding: 20px 16px;
  max-width: 600px;
  margin: 0 auto;
}
```

**Сохранение настроек:**

```javascript
async function save() {
  const body = {
    latitude: parseFloat(document.getElementById('lat').value),
    units: document.getElementById('units').value,
  };

  // ❌ Неправильно — напрямую в файл нельзя
  // localStorage.setItem('config', JSON.stringify(body));

  // ✅ Правильно — через API модуля
  const r = await fetch(BASE + '/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (r.ok) showMsg('Saved!', 'ok');
  else      showMsg('Error: ' + r.status, 'err');
}
```

---

## 10. Полный шаблон widget.html

Готовый минимальный шаблон для копирования:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>My Widget</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    /* Обязательно: заполнить iframe без overflow */
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

    /* ── Состояния загрузки / ошибки ── */
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

    /* ── Твои стили ── */

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
  // ── BASE URL — единственный правильный способ ──────────────────────────
  const BASE = window.location.pathname
    .replace(/\/(widget|settings)(\.html)?(\?.*)?$/, '');

  // ── ui_token для запросов к Core API ──────────────────────────────────
  const uiToken = new URLSearchParams(window.location.search).get('ui_token');

  // ── Адаптация под размер ячейки ────────────────────────────────────────
  function checkLayout() {
    const root = document.getElementById('root');
    if (!root) return;
    root.classList.toggle('compact', root.offsetHeight < 160);
  }

  // ── Рендер (твой код) ──────────────────────────────────────────────────
  function render(data) {
    const root = document.getElementById('root');
    root.innerHTML = `
      <div>Данные: ${JSON.stringify(data)}</div>
    `;
    checkLayout();
  }

  // ── Загрузка данных ────────────────────────────────────────────────────
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

  // ── Инициализация ──────────────────────────────────────────────────────
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

## 11. Полный шаблон settings.html

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Settings</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    /* settings — прокручиваемое модальное окно, не фиксированная ячейка */
    html, body {
      width: 100%;
      min-height: 100%;     /* ← не 100vh */
      overflow-y: auto;     /* ← скролл разрешён */
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
  <h1>⚙️ Module Settings</h1>

  <div class="section">
    <h2>Configuration</h2>

    <label>Parameter 1</label>
    <input type="text" id="param1" placeholder="Value">

    <label>Parameter 2</label>
    <select id="param2">
      <option value="a">Option A</option>
      <option value="b">Option B</option>
    </select>
  </div>

  <button class="btn btn-full" onclick="save()">💾 Save</button>
  <button class="btn btn-ghost btn-full" style="margin-top:8px" onclick="loadStatus()">🔄 Reload</button>

  <div id="msg"></div>
</div>

<script>
  const BASE = window.location.pathname
    .replace(/\/(widget|settings)(\.html)?(\?.*)?$/, '');

  // ── Загрузить текущие настройки ────────────────────────────────────────
  async function loadStatus() {
    try {
      const s = await fetch(BASE + '/status').then(r => r.json());
      if (s.param1) document.getElementById('param1').value = s.param1;
      if (s.param2) document.getElementById('param2').value = s.param2;
    } catch (e) {
      showMsg('Load failed: ' + e.message, 'err');
    }
  }

  // ── Сохранить ──────────────────────────────────────────────────────────
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

## 12. Чеклист перед сдачей

**CSS:**
- [ ] `html, body { width: 100%; height: 100%; overflow: hidden; }` — в widget.html
- [ ] Нет `100vh`, `min-height: 100vh` в widget.html
- [ ] `.root { width: 100%; height: 100%; display: flex; overflow: hidden; }`
- [ ] Нет `position: fixed` элементов в widget.html
- [ ] settings.html использует `min-height: 100%` и `overflow-y: auto`

**JavaScript:**
- [ ] `BASE` вычисляется через `window.location.pathname.replace(...)`
- [ ] Нет хардкода `localhost:PORT` или IP-адресов
- [ ] Нет `fetch('/endpoint')` без BASE префикса
- [ ] Есть обработка ошибок fetch (try/catch + показ состояния ошибки)
- [ ] Есть автообновление данных через `setInterval` (или SSE)
- [ ] Есть `window.addEventListener('resize', checkLayout)` для адаптации

**Функциональность:**
- [ ] Виджет показывает состояние загрузки (`Loading...`) пока нет данных
- [ ] Виджет показывает состояние ошибки если API недоступен
- [ ] При пустых данных (`null`, `undefined`) нет падения — показывается `'—'`
- [ ] Компактный режим при `height < 160px` скрывает второстепенный контент

**Соответствие размеру:**
- [ ] Контент помещается в ячейку без скролла при базовом размере
- [ ] При уменьшении iframe (compact) ничего не обрезается некрасиво

---

## 13. Типичные ошибки

### ❌ Белая полоса снизу / контент не заполняет ячейку

**Причина:** `body` имеет стандартный отступ или `height` не задан.

```css
/* Исправление */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 100%; height: 100%; }
```

---

### ❌ Полоса прокрутки появляется в ячейке дашборда

**Причина:** контент выходит за `height: 100%`, а `overflow: hidden` не задан.

```css
/* Исправление */
html, body { overflow: hidden; }
.root      { overflow: hidden; }
```

---

### ❌ Прогноз / список обрезается сверху или снизу

**Причина:** flex-дочерний элемент не может сжаться меньше своего содержимого.

```css
/* Исправление */
.forecast {
  flex: 1;
  min-height: 0;  /* ← ключевое свойство, позволяет flex-item сжиматься */
  overflow: hidden;
}
```

---

### ❌ Запросы к API падают с CORS или 404

**Причина:** неправильно вычислен BASE URL (хардкод порта или origin без пути).

```javascript
// Исправление
const BASE = window.location.pathname
  .replace(/\/(widget|settings)(\.html)?(\?.*)?$/, '');
```

---

### ❌ Модуль показывает старые данные после изменения настроек

**Причина:** нет перезагрузки данных после сохранения в settings.

```javascript
// В settings.html — после успешного save():
async function save() {
  // ... POST /config ...
  showMsg('Saved!', 'ok');
  // Перезагрузить статус чтобы отразить изменения
  await loadStatus();
}
```

---

### ❌ TypeError: Cannot read properties of null / undefined

**Причина:** данные пришли, но поле отсутствует — нет защиты от null.

```javascript
// ❌ Падает если temperature == null
document.getElementById('temp').textContent = Math.round(data.temperature) + '°';

// ✅ Безопасно
const t = data.temperature;
document.getElementById('temp').textContent =
  t != null ? Math.round(t) + '°' : '—';
```

---

*SelenaCore · Widget Development Guide · MIT*
