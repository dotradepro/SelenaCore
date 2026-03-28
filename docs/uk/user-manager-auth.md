# User Manager — Автентифікація та авторизація

🇬🇧 [English version](../user-manager-auth.md)

**Модуль:** `system_modules/user_manager/`
**Маршрути:** `/api/ui/modules/user-manager/`
**Тип:** SYSTEM (in-process, без порту)

---

## Огляд

User Manager відповідає за управління ідентифікацією та контроль доступу в SelenaCore. Надає:

- **Токен пристрою** (device token) — довготривала автентифікація (HttpOnly cookie + заголовок)
- **Підвищені сесії** (elevated session) для чутливих операцій (PIN або QR, діє 5 хв з ковзним вікном)
- **CRUD користувачів** — плоска модель: перший користувач = `admin`, решта = `resident` (мешканці)
- **QR-реєстрація пристрою** (новий браузер) та **QR-розблокування кіоска** (elevate)
- **Підтвердження PIN** для швидкого підвищення без QR

**Ніяких рольових дозволів.** Гейт PIN/QR — єдиний механізм контролю доступу.
Будь-який авторизований користувач може керувати налаштуваннями, користувачами та пристроями.

Усі токени генеруються випадково, зберігаються у вигляді SHA-256 хешу у SQLite (`/var/lib/selena/selena.db`).
Шлях до бази даних задається як **абсолютний** через `sqlite+aiosqlite:////var/lib/selena/selena.db`
(4 слеші = абсолютний шлях; 3 слеші = відносний від CWD контейнера).

---

## Модель автентифікації

```
Браузер / Телефон
      │
      │  X-Device-Token: <device_token>    (заголовок)
      │  Cookie: selena_device=<device_token>  (HttpOnly)
      │
      ▼
 user_manager → DeviceManager.verify(token)
      │
      ├─ токен дійсний  → повертає {user_id, role, display_name, ...}
      └─ токен недійсний → 401 Unauthorized


Чутлива операція (PATCH /users, DELETE /users, зміна PIN тощо)
      │
      │  X-Elevated-Token: <elevated_token>   (заголовок)
      │
      ▼
 _require_elevated() перевіряє:
      ├─ токен дійсний + не протермінований (< 5 хв, ковзне вікно) → виконати
      └─ відсутній / протермінований → 403 Forbidden
```

### Типи токенів

| Токен | Зберігається | Термін дії | Призначення |
|-------|-------------|-----------|-------------|
| `device_token` | HttpOnly cookie + `localStorage('selena_device')` | 30 днів | Ідентифікує зареєстрований браузер/телефон |
| `elevated_token` | Лише в `sessionStorage('selena_elevated')` | 5 хв (ковзне) | Дозволяє чутливі операції |
| `qr_session` | In-memory словник у user-manager | 5 хв (`_QR_TTL = 300`) | Одноразове QR-рукостискання |

---

## Модель користувачів

| Тип | Опис |
|-----|------|
| `admin` | Перший користувач, створений під час визарда. Має PIN. |
| `resident` | Усі наступні користувачі (мешканці будинку). Створюються з ім'ям + опційна прив'язка пристрою. |

Ніяких перевірок дозволів на основі ролей. Поле `role` зберігається в базі даних
(`admin` або `resident`), але не впливає на авторизацію. Гейт підвищення (PIN/QR) —
єдиний механізм контролю доступу для всіх розділів окрім Дашборда.

---

## Реєстрація пристрою (новий браузер/телефон)

Новий пристрій реєструється, вказуючи ім'я користувача та PIN.

### Варіант A — Пряма реєстрація (AuthWall)

```
Браузер                       user-manager
   │                               │
   │── POST /auth/device/register ─►│
   │   {username, pin, device_name} │
   │                               │── перевірка облікових даних
   │                               │── створення запису пристрою
   │◄── 201 {device_token} ─────────│
   │    Set-Cookie: selena_device   │
```

### Варіант Б — QR-реєстрація (сканування телефоном)

```
Браузер ПК                    user-manager               Телефон
      │                            │                        │
      │── POST /auth/qr/start ────►│                        │
      │   {mode:"access"}          │── створення сесії ─────│
      │◄── {session_id,            │   термін 5 хв          │
      │     qr_image,              │                        │
      │     join_url,              │   QR містить LAN IP*   │
      │     expires_in:300}        │                        │
      │                            │                        │
      │  [показує QR на екрані]    │    [Телефон сканує]    │
      │                            │                        │
      │                            │◄── GET /auth/qr/join/{id}  ─── Браузер телефону
      │                            │    → qr_join.html
      │                            │◄── POST /auth/qr/complete/{id}
      │                            │    {username, pin, device_name}
      │                            │── перевірка + device_token для телефону
      │                            │── сесія → "complete"
      │                            │
      │── GET /auth/qr/status/{id}►│  (опитування кожні 2с)
      │◄── {status:"complete",     │
      │     device_token:"..."}    │
```

`*` **Визначення LAN IP:** коли `qr_start` викликається з localhost/127.0.0.1 (кіоск), сервер визначає реальний LAN IP через UDP-зонд і підставляє його в `join_url`.

---

## Розблокування кіоска через QR (elevate)

На екрані пристрою `KioskElevationGate` перекриває обмежені розділи. Два способи розблокування:

### Розблокування через PIN

```
Кіоск                         user-manager
   │                               │
   │── POST /auth/pin/confirm ─────►│
   │   X-Device-Token: <token>      │── перевірка токену пристрою
   │   {pin}                        │── перевірка PIN-хешу
   │◄── 200 {elevated_token} ───────│
   │    (сесія 5 хв, ковзне вікно) │
```

### Розблокування через QR

```
Кіоск                         user-manager               Телефон
   │                               │                        │
   │── POST /auth/qr/start ────────►│                        │
   │   {mode:"elevate"}            │── створення сесії ─────│
   │◄── {session_id,               │   mode="elevate"       │
   │     qr_image (LAN IP),        │   термін 5 хв          │
   │     expires_in:300}           │                        │
   │                               │   QR на екрані кіоска  │
   │  [кіоск показує QR+таймер]   │    [Телефон сканує]    │
   │                               │                        │
   │                               │◄── POST /auth/qr/approve/{id}
   │                               │    X-Device-Token: <phone_token>
   │                               │── перевірка → elevated_token
   │                               │── сесія → "complete"
   │                               │
   │── GET /auth/qr/status/{id} ──►│  (опитування кожні 2с)
   │◄── {status:"complete",        │
   │     elevated_token:"..."}     │
   │                               │
   │  [замок знімається]           │  [Телефон: "Авторизацію підтверджено"]
```

### Розблокування QR через presence (без токена пристрою)

```
Кіоск                         user-manager               Телефон (відстежуваний)
   │                               │                        │
   │── POST /auth/qr/start ────────►│                        │
   │   {mode:"elevate"}            │── створення сесії ─────│
   │◄── {session_id, qr_image}     │                        │
   │                               │   QR на екрані кіоска  │
   │  [кіоск показує QR+таймер]   │    [Телефон сканує]    │
   │                               │                        │
   │                               │◄── POST /auth/qr/approve-by-presence/{id}
   │                               │    (токен не потрібен — сервер визначає
   │                               │     IP → ARP MAC → presence БД → прив'язаний акаунт)
   │                               │── перевірка → elevated_token
   │                               │── сесія → "complete"
   │                               │
   │── GET /auth/qr/status/{id} ──►│  (опитування кожні 2с)
   │◄── {status:"complete",        │
   │     elevated_token:"..."}     │
```

Цей флоу працює коли телефон є відстежуваним presence-пристроєм (його MAC є в
базі presence-detection з `linked_account_id`). Токен пристрою чи PIN не потрібні —
ідентифікація через мережевий рівень.

---

## Навігація UI

Інтерфейс не має бічної панелі. Навігація:

- **Логотип** (ліворуч у TopBar) — клік повертає на Дашборд
- **Шестеренка** (праворуч у TopBar, біля годинника) — відкриває Налаштування (потрібен PIN/QR)
- **Дашборд** (`/`) — завжди доступний, без автентифікації
- **Налаштування** (`/settings/*`) — захищено `KioskElevationGate`, містить усі розділи:
  - Зовнішній вигляд, Голос, Аудіо, Мережа, Користувачі, Модулі, Система, Інфо системи, Цілісність, Безпека, Системні модулі

---

## Довідник API

Базовий шлях: `/api/ui/modules/user-manager`

### Автентифікація — реєстрація пристрою

| Метод | Шлях | Auth | Опис |
|-------|------|------|------|
| `POST` | `/auth/setup` | — | Створення першого адміна (тільки при пустій БД) |
| `POST` | `/auth/device/register` | — | Реєстрація нового пристрою (username + PIN) |
| `POST` | `/auth/pin/confirm` | токен пристрою | Перевірка PIN → отримання elevated_token |
| `POST` | `/auth/device/verify` | токен пристрою | Перевірка токена, повертає інфо |
| `DELETE` | `/auth/device` | токен пристрою | Відкликання власного токена |

### Автентифікація — QR-флоу

| Метод | Шлях | Auth | Опис |
|-------|------|------|------|
| `POST` | `/auth/qr/start` | — | Створення QR-сесії (`mode`: `access`, `elevate`, `invite`, `wizard_setup`) |
| `GET`  | `/auth/qr/status/{id}` | — | Опитування статусу сесії |
| `GET`  | `/auth/qr/info/{id}` | — | Режим + `expires_in_seconds` |
| `GET`  | `/auth/qr/join/{id}` | — | Сторінка підтвердження (HTML) |
| `POST` | `/auth/qr/approve/{id}` | токен пристрою | Підтвердження розблокування (elevate) |
| `POST` | `/auth/qr/approve-by-presence/{id}` | — | Підтвердження через presence (IP → MAC → акаунт) |
| `POST` | `/auth/qr/complete/{id}` | — | Телефон відправляє username+PIN (access/invite) |
| `POST` | `/auth/qr/wizard-link/{id}` | — | Прив'язка телефону під час визарда + авто-додавання в presence |

### Автентифікація — Ідентифікація телефону через presence

| Метод | Шлях | Auth | Опис |
|-------|------|------|------|
| `POST` | `/auth/phone/identify` | — | Ідентифікація телефону: IP → MAC → presence user → прив'язаний акаунт |

### Автентифікація — Підвищена сесія

| Метод | Шлях | Auth | Опис |
|-------|------|------|------|
| `POST` | `/auth/elevated/refresh` | elevated token | Оновлення TTL (ковзне вікно) |
| `POST` | `/auth/elevated/revoke` | elevated token | Негайне скасування сесії |

### Автентифікація — Тимчасові сесії браузера

| Метод | Шлях | Auth | Опис |
|-------|------|------|------|
| `POST` | `/auth/session/heartbeat` | session token | Оновлення таймера QR-сесії |
| `POST` | `/auth/session/logout` | session token | Завершення тимчасової сесії |

### CRUD користувачів

| Метод | Шлях | Auth | Опис |
|-------|------|------|------|
| `GET`    | `/users` | токен пристрою | Список усіх користувачів |
| `POST`   | `/users` | токен пристрою + elevated | Створення користувача (ім'я + PIN) |
| `GET`    | `/users/{id}` | токен пристрою | Отримати користувача за ID |
| `PATCH`  | `/users/{id}` | токен пристрою + elevated | Оновити (display_name) |
| `DELETE` | `/users/{id}` | токен пристрою + elevated | Деактивувати |
| `POST`   | `/users/{id}/pin` | токен пристрою + elevated | Змінити PIN |

### Пристрої

| Метод | Шлях | Auth | Опис |
|-------|------|------|------|
| `GET`    | `/users/{id}/devices` | токен пристрою | Список пристроїв користувача |
| `DELETE` | `/devices/{id}` | токен пристрою | Відкликати пристрій |
| `PATCH`  | `/devices/{id}` | токен пристрою | Перейменувати пристрій |

### Система

| Метод | Шлях | Auth | Опис |
|-------|------|------|------|
| `GET` | `/me` | — | Інфо про поточного користувача |
| `GET` | `/auth/status` | — | Чи потрібне первинне налаштування |

---

## KioskElevationGate

React-компонент (`src/components/KioskElevationGate.tsx`), що перекриває обмежені розділи UI.

**Застосовується до всіх сесій** — немає "довірених" браузерів.

**Обмежені маршрути**: `/settings/*` (включає Модулі, Інфо системи, Цілісність як вкладки)
**Завжди доступно**: `/` (дашборд)

**Поведінка:**
1. Користувач натискає шестеренку в TopBar
2. Оверлей замка перекриває екран
3. Розблокування через вкладку PIN або QR
4. Elevated token зберігається в `sessionStorage` + Zustand store
5. При успіху: оверлей зникає, розділ відкривається
6. Неактивність 5 хв → сесія скасована → замок з'являється

**Блокування при неактивності (5 хв):**
- Frontend: `setTimeout` debounce — скидається при `click`, `keypress`, `mousemove`, `scroll`
- Frontend: пінг `POST /auth/elevated/refresh` кожну хвилину при активності
- Backend: ковзне вікно — `verify()` подовжує TTL при кожному валідному виклику
- При тайм-ауті: `POST /auth/elevated/revoke` + скидання + редирект на `/`

---

## Нотатки з безпеки

- `device_token` генерується через `uuid.uuid4()` — зберігається лише SHA-256 хеш
- `elevated_token` — окремий короткочасний токен, не перевикористовується
- QR-сесії зберігаються лише в пам'яті; перезапуск очищує всі
- `qr_join.html` зчитує токен з `localStorage` / cookie — токен **ніколи** не в URL
- PATCH/DELETE вимагає одночасно device_token **та** elevated_token
- Захист від брутфорсу PIN: 5 спроб → блок 10 хв (per-user, in-memory)

---

## Карта файлів

```
system_modules/user_manager/
  module.py          — FastAPI маршрути, QR-логіка, визначення LAN IP
  profiles.py        — UserManager: CRUD, хешування PIN (модель admin/resident)
  devices.py         — DeviceManager: створення токена, верифікація, відкликання
  elevated.py        — ElevatedManager: короткочасні elevated-токени (5 хв TTL)
  pin_auth.py        — Rate limiting PIN (5 спроб → блок 10 хв)
  sessions.py        — BrowserSessionManager: тимчасові QR-сесії браузера
  face_auth.py       — Реєстрація/верифікація обличчя
  audit_log.py       — Журнал аудиту
  qr_join.html       — Сторінка підтвердження на телефоні (HTML, EN/UK)
  manifest.json      — Тип SYSTEM, без поля port

src/components/
  KioskElevationGate.tsx — Оверлей замка (вкладки PIN + QR, таймер)
  Layout.tsx             — TopBar з логотипом (→ home), шестеренкою (→ settings)
  Settings.tsx           — Усі вкладки адміністрування
  UsersPanel.tsx         — Керування користувачами (створення мешканців, прив'язка пристроїв)

src/hooks/
  useElevated.ts         — Керування elevated-токеном
  useKioskInactivity.ts  — Автоблокування через 5 хв неактивності
  useSessionKeepAlive.ts — Heartbeat QR-сесії
```
