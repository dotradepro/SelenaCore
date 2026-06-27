# Архітектура синхронізації UI

> Синхронізація стану UI (тема, мова, розташування віджетів) у реальному часі між усіма підключеними клієнтами: екран кіоску, браузер телефону, браузер ПК.

---

## Огляд

SelenaCore обслуговує React SPA та всі API-ендпоінти з **єдиного процесу** на порту **80**. Синхронізація стану між клієнтами відбувається через **WebSocket** `/api/ui/sync` з версіонуванням стану, повним знімком при підключенні та дельта-повтором при перепідключенні.

```
Браузер/Кіоск/Телефон
      |
      | WebSocket /api/ui/sync?v=<остання_версія>
      |
Core API :80 (єдиний: API + SPA + WebSocket)
      |
      SyncManager (синглтон)
        |- _version: int (монотонний лічильник)
        |- _settings: {theme, language}
        |- _layout: {pinned, sizes, positions, ...}
        |- _event_log: deque(maxlen=256)
        |- _clients: dict[id, WebSocket]
```

HTTPS на порту 443 обслуговується легким Python TLS-проксі (~5 МБ RAM), який перенаправляє на основний процес :80.

---

## Проблема (до рефакторингу)

| Проблема | Причина |
|----------|---------|
| Екран кіоску зависає | SSE через httpx-проксі створював zombie-з'єднання без детекції |
| Тема/мова не синхронізується | `asyncio.QueueFull` мовчки втрачав SSE-події (maxsize=64) |
| Різний стан на різних клієнтах | Немає відновлення стану після реконнекту EventSource |
| ~3 ГБ RAM | Два повних uvicorn-процеси (Core :7070 + UI :80), кожен завантажує всі модулі |

### Попередня архітектура

```
Браузер --> UI-сервер :80 (httpx проксі) --> Core API :7070
            |                                     |
            |- SSE /api/ui/stream (з втратами)    |- EventBus
            |- Статичні файли (SPA)               |- Роутери модулів
            |- 60-90 МБ RAM даремно               |- Вся бізнес-логіка
```

---

## Рішення

### Єдиний сервер (один процес)

```
Браузер --> Core API :80 (напряму)
            |
            |- API-маршрути (/api/v1/*, /api/ui/*)
            |- Роутери модулів (/api/ui/modules/{name}/*)
            |- WebSocket /api/ui/sync (версіонований стан)
            |- Статичні файли SPA (/assets/*, /icons/*, /*)
            |- PWA (/manifest.json, /sw.js)

HTTPS :443 --> TLS-проксі (asyncio, ~5 МБ) --> :80
```

### Економія ресурсів

| Метрика | До | Після |
|---------|-----|-------|
| Python-процеси | 2-3 | 1 + TLS-проксі |
| Використання RAM | ~3 ГБ | ~1.5 ГБ |
| Затримка проксі на запит | 100-200 мс | 0 мс |
| Детекція zombie-з'єднань | Немає | WebSocket ping/pong (5с) |
| Відновлення стану при реконнекті | Немає | Повний знімок або дельта-повтор |

---

## Протокол WebSocket синхронізації

### Ендпоінт

```
ws://host/api/ui/sync?v=<остання_відома_версія>
wss://host/api/ui/sync?v=<остання_відома_версія>   (через TLS-проксі)
```

### Процес з'єднання

```
1. Клієнт підключається з v=0 (перший раз) або v=N (реконнект)

2. Сервер надсилає початковий стан:

   Якщо v=0 або версія занадто стара:
   <- {"type": "hello", "version": 5,
       "settings": {"theme": "dark", "language": "uk"},
       "layout": {"pinned": [...], "sizes": {...}, ...}}

   Якщо v>0 і події доступні в лозі:
   <- {"type": "replay", "events": [
        {"version": 3, "event_type": "settings_changed", "payload": {"theme": "dark"}},
        {"version": 4, "event_type": "layout_changed", "payload": {...}}
      ]}

3. Сервер надсилає дельта-події при зміні:
   <- {"type": "event", "version": 6,
       "event_type": "settings_changed",
       "payload": {"language": "en"}}

4. Сервер надсилає ping кожні 5 секунд:
   <- {"type": "ping", "version": 6, "ts": 1712345678.0}

5. Клієнт повинен відповісти pong:
   -> {"type": "pong"}
   (немає pong протягом 15с -> сервер закриває з'єднання)

6. При розриві клієнт перепідключається з v=<lastVersion>
   і отримує лише пропущені події (або повний знімок)
```

### Типи подій

| Подія | Тригер | Payload |
|-------|--------|---------|
| `settings_changed` | `POST /api/ui/settings` | `{theme?, language?}` |
| `layout_changed` | `POST /api/ui/layout` | Повний об'єкт layout |
| `module.started` | Життєвий цикл модуля | `{name}` |
| `module.stopped` | Життєвий цикл модуля | `{name}` |
| `module.removed` | Життєвий цикл модуля | `{name}` |

---

## Ключові компоненти

### SyncManager (`core/api/sync_manager.py`)

Синглтон, що зберігає авторитетний стан UI:

- **Версіонований стан**: монотонний лічильник, збільшується при кожній зміні
- **Лог подій**: `deque(maxlen=256)` — зберігає останні події для повтору (~50-100 КБ)
- **Реєстр клієнтів**: відстежує WebSocket-клієнтів з часом останнього pong
- **Подвійна трансляція**: надсилає події і WebSocket-клієнтам, і legacy SSE-клієнтам

```python
from core.api.sync_manager import get_sync_manager

manager = get_sync_manager()
manager.get_snapshot()                    # Повний стан для нових клієнтів
manager.get_events_since(version=5)       # Дельта-повтор
await manager.update_settings({"theme": "dark"})  # Публікація зміни
await manager.update_layout(layout_dict)           # Публікація layout
```

### Ендпоінт налаштувань

```http
GET /api/ui/settings
-> {"theme": "auto", "language": "uk"}

POST /api/ui/settings
Content-Type: application/json
{"theme": "dark"}
-> {"ok": true}
(транслює settings_changed всім клієнтам через WebSocket + SSE)
```

### Watchdog кіоску (`src/hooks/useConnectionHealth.ts`)

React-хук, що моніторить здоров'я WebSocket:
- Перевіряє `lastServerContact` кожні 10 секунд
- Якщо немає контакту 60 секунд — примусовий `window.location.reload()`
- Гарантує, що екран кіоску ніколи не показує застарілий стан

### TLS-проксі

Легкий asyncio-скрипт (вбудований в `scripts/start.sh`):
- Слухає на :443 з SSL-контекстом
- Перенаправляє TCP-з'єднання на :80
- ~5 МБ RAM (замість ~1.5 ГБ для другого uvicorn)

---

## Роздача статичних файлів SPA

React SPA обслуговується напряму Core API (`core/main.py`):

- `/assets/*` — Vite-зібрані JS/CSS (NoCacheStaticFiles)
- `/icons/*` — PWA-іконки (NoCacheStaticFiles)
- `/manifest.json` — PWA Web App Manifest
- `/sw.js` — Service Worker
- `/{будь_який_шлях}` — SPA catch-all повертає `index.html`

SPA catch-all реєструється **після** всіх модульних роутерів (під час lifespan startup), щоб не перехоплювати `/api/ui/modules/{name}/*`.

Джерело: `system_modules/ui_core/static/` (збирається через `npx vite build`)

---

## Застарілі компоненти

| Файл | Статус | Заміна |
|------|--------|--------|
| `system_modules/ui_core/server.py` | Видалено | Core API обслуговує SPA напряму |
| `system_modules/ui_core/routes/dashboard.py` | Видалено | Маршрути в `core/api/routes/ui.py` |
| `system_modules/ui_core/wizard.py` | Видалено | Маршрути в `core/api/routes/ui.py` |
| SSE `/api/ui/stream` | Залишено (сумісність) | WebSocket `/api/ui/sync` |

---

## Нотатки міграції

### Зміна порту: 7070 -> 80

Усі посилання на порт 7070 оновлені:
- `core/config.py`: `core_port` за замовчуванням = 80
- `scripts/start.sh`: один uvicorn на :80
- `Dockerfile.core`: `EXPOSE 80 443`
- `docker-compose.yml`: healthcheck на `http://localhost/api/v1/health`
- `smarthome-core.service`: `--port 80`
- Усі `main.py` системних модулів: `localhost:7070` -> `localhost`

### Після `docker compose up -d --build`

Файл `start.sh` **копіюється** в Docker-образ (не volume-mount). Після редагування `scripts/start.sh`:
- Перезбірка: `docker compose up -d --build`
- Або вручну: `docker cp scripts/start.sh selena-core:/opt/selena-core/start.sh && docker restart selena-core`

### Очищення кешу байткоду

Після зміни портів у Python-файлах очистіть `__pycache__`:
```bash
docker exec selena-core find /opt/selena-core -name "__pycache__" -type d -exec rm -rf {} +
docker restart selena-core
```
