# SmartHome LK Core — Технічне завдання v0.3-beta
**Дата:** 2026-03-20 | **Ліцензія:** Open Source (MIT) | **Статус:** DRAFT

---

## Зміст

1. Вступ і концепція
2. Архітектура: 2-контейнерна схема
3. Класифікація модулів
4. Системні модулі ядра
5. Перший запуск — Onboarding Wizard
6. ОС і режими UI
7. Голосовий асистент і LLM
8. Аудіо-підсистема
9. Користувачі, авторизація, аудит
10. Мережа, безпека, віддалений доступ
11. Сповіщення
12. Імпорт з існуючих систем
13. Моніторинг ресурсів і деградація
14. Python SDK для розробників модулів
15. Офлайн-режим
16. Критерії готовності (Definition of Done)
17. Out of Scope

---

## 1. Вступ і концепція

SmartHome LK Core — відкритий (Open Source, MIT) локальний хаб розумного дому. Встановлюється на Raspberry Pi 4/5 або будь-який SBC під керуванням Linux. Не потребує підписки для базової роботи. Інтегрується з платформою SmartHome LK для хмарних функцій, маркетплейсу модулів і віддаленого керування.

### 1.1 Три фундаментальних принципи

**Ядро незмінне** — файли ядра захищені SHA256-еталоном і прапорцем `chattr +i`. Зміна неможлива без явного оновлення через офіційний канал платформи.

**Модулі ізольовані** — вся стороння логіка виконується в ізольованих Python-потоках усередині контейнера модулів. Спілкування з ядром лише через Core API (HTTP, localhost:7070). Прямий доступ до даних ядра і розділу `/secure` заборонений.

**Агент спостерігає** — незалежний процес `IntegrityAgent` безперервно перевіряє SHA256-хеші файлів ядра і реагує за ланцюжком: стоп модулів → сповіщення платформи → відкат з резервної копії → SAFE MODE.

### 1.2 Open Source

Проєкт розповсюджується під ліцензією MIT. UPS/резервне живлення, кастомні апаратні конфігурації — на розсуд спільноти. ТЗ фіксує базову функціональність ядра.

---

## 2. Архітектура: 2-контейнерна схема

Замість окремого Docker-контейнера на кожен модуль використовується мінімальна схема.

| Контейнер | RAM | Призначення |
|---|---|---|
| `smarthome-core` | ~420 MB | Ядро: FastAPI, Device Registry, Event Bus, Module Loader, Cloud Sync, Voice Core, LLM Engine, UI Core |
| `smarthome-modules` | 180–350 MB | Всі користувацькі модулі в одному Python-процесі через Plugin Manager |
| `smarthome-sandbox` | 96–256 MB, `--rm` | Тимчасовий: тест нового модуля перед встановленням. Автовидаляється. |

### 2.1 Plugin Manager

Plugin Manager — компонент ядра, що завантажує модулі як Python-класи через `importlib` в ізольований namespace з окремим потоком (Thread).

- Збій модуля (Exception) → перехоплюється, логується, перезапускається тільки цей модуль
- OOM / segfault → падає контейнер `smarthome-modules` цілком → systemd автоматично піднімає
- Hot-reload: оновлення модуля через `importlib.reload()` без перезапуску контейнера
- Ліміт пам'яті на модуль: `resource.setrlimit` + `tracemalloc` моніторинг усередині потоку

### 2.2 Watchdog — дворівневий захист

- **Рівень 1 — systemd**: `smarthome-core.service` і `smarthome-modules.service` з `Restart=always`, `RestartSec=5s`
- **Рівень 2 — Docker**: `--restart=unless-stopped` на обох контейнерах
- **Integrity Agent**: окремий `smarthome-agent.service`, незалежний від обох контейнерів

### 2.3 Економія пам'яті vs окремі контейнери

| Конфігурація | RAM (типове навантаження) |
|---|---|
| По контейнеру на кожен модуль (8 модулів) | ~1 200 MB |
| 2-контейнерна схема (ті ж 8 модулів) | ~620 MB |
| Економія | ~580 MB (−48%) |

---

## 3. Класифікація модулів

### 3.1 Типи модулів

| Тип | Видалити? | Опис |
|---|---|---|
| `SYSTEM` | Не можна | Постачається з ядром. Розширені привілеї. Запускається в процесі ядра. |
| `UI` | Можна | Іконка в меню + віджет на дашборді + сторінка налаштувань. iframe sandbox. |
| `INTEGRATION` | Можна | Зовнішні сервіси через OAuth/API. Токени в ядрі, модуль не бачить їх напряму. |
| `DRIVER` | Можна | Драйвер протоколу: Zigbee, Z-Wave, MQTT, HTTP-пристрої. |
| `AUTOMATION` | Можна | Сценарії без UI. Event listeners + scheduler. Найлегші (~40 MB). |
| `IMPORT_SOURCE` | Можна | Імпорт з Home Assistant, Tuya, Philips Hue та інших систем. |

### 3.2 UI-профілі

| Профіль | Компоненти | Приклад |
|---|---|---|
| `HEADLESS` | Немає UI | Нічний режим, alarm |
| `SETTINGS_ONLY` | Сторінка налаштувань | Системний модуль voice-core |
| `ICON_SETTINGS` | Іконка + налаштування | Gmail-інтеграція |
| `FULL` | Іконка + віджет + налаштування | Модуль клімату, освітлення |

### 3.3 Runtime-режими (manifest.json)

- `always_on` — запущений постійно. UI-модулі, драйвери, Telegram-сповіщення.
- `on_demand` — потік стартує за ~50 мс, виконує задачу, зупиняється. AUTOMATION.
- `scheduled` — cron-рядок у manifest. Приклад: `"*/5 * * * *"` для перевірки Gmail кожні 5 хвилин.

### 3.4 manifest.json — структура

```yaml
name:          my-module          # унікальне ім'я (snake_case)
version:       1.0.0              # semver
type:          INTEGRATION        # тип модуля
ui_profile:    ICON_SETTINGS      # UI-профіль
api_version:   "1.0"
runtime_mode:  scheduled
schedule:      "*/5 * * * *"
permissions:
  - device.read
  - events.subscribe
port:          8100               # порт HTTP-сервера модуля

# Якщо ui_profile != HEADLESS:
ui:
  icon:     icon.svg
  widget:
    file:   widget.html
    size:   "2x1"                 # 1x1 | 2x1 | 2x2 | 4x1
  settings: settings.html

# Якщо тип INTEGRATION:
oauth:
  provider: google                # google | telegram | custom
  scopes:
    - gmail.readonly
```

### 3.5 Безпека UI-компонентів

- Всі віджети та сторінки налаштувань рендеряться в `<iframe sandbox>` — модуль не має доступу до DOM ядра
- Спілкування тільки через `window.postMessage` з whitelist дозволених типів повідомлень
- CSP заголовок: `default-src 'self'` — заборона inline scripts

---

## 4. Системні модулі ядра

| Модуль | UI-профіль | Функція |
|---|---|---|
| `voice-core` | SETTINGS_ONLY | STT (Whisper.cpp), TTS (Piper), wake-word, speaker ID, режим приватності |
| `llm-engine` | SETTINGS_ONLY | Ollama, Intent Router (Fast Matcher + LLM), вибір і завантаження моделей |
| `network-scanner` | SETTINGS_ONLY | ARP sweep, mDNS, SSDP/UPnP, Zigbee/Z-Wave, OUI класифікація |
| `user-manager` | SETTINGS_ONLY | Профілі, ролі, голосові зліпки, відеоавторизація, аудит-лог |
| `secrets-vault` | HEADLESS | AES-256-GCM сховище OAuth-токенів, proxy для модулів |
| `backup-manager` | SETTINGS_ONLY | Локальний бекап (USB/SD) + хмара E2E, QR-перенесення секретів |
| `remote-access` | HEADLESS | Tailscale VPN клієнт: автопідключення, статус тунелю |
| `hw-monitor` | HEADLESS | Температура CPU, RAM, диск. Алерт + автозниження навантаження при перегріві |
| `notify-push` | HEADLESS | Web Push VAPID — сповіщення на телефон коли браузер закритий |
| `ui-core` | FULL | PWA · smarthome.local:8080 · TTY1/kiosk · wizard першого запуску |

---

## 5. Перший запуск — Onboarding Wizard

Мета: користувач без технічних знань налаштовує систему за 5–10 хвилин використовуючи тільки телефон.

### 5.1 Крок 0 — До ввімкнення: запис образу на SD

- Готовий `.img` образ (SmartHome LK OS Lite) завантажується з сайту платформи
- Записується через Raspberry Pi Imager або balenaEtcher — без додаткових налаштувань
- Образ: Raspberry Pi OS Lite + Docker + smarthome-core попередньо встановлені

### 5.2 Крок 1 — Перше ввімкнення: точка доступу + QR

При першому старті (або якщо Wi-Fi не налаштовано) ядро піднімає точку доступу:

```
SSID:     SmartHome-Setup
Password: smarthome
```

**Якщо підключений HDMI-дисплей:**
→ QR-код відображається на TTY1
→ Скануй → потрапляєш у wizard у браузері телефону

**Якщо немає екрану (headless):**
→ Підключись до SmartHome-Setup з телефону
→ Відкрий браузер → `192.168.4.1`
→ Той самий wizard

mDNS fallback: `http://smarthome-setup.local`

### 5.3 Wizard — 9 кроків у браузері телефону

| # | Крок | Деталі |
|---|---|---|
| 1 | **Мова інтерфейсу** | Вибір: ru / uk / en. Впливає на всі тексти і TTS-голоси. |
| 2 | **Wi-Fi мережа** | Список знайдених мереж. Введення пароля. Pi підключається і перевіряє інтернет. |
| 3 | **Ім'я пристрою** | Наприклад "Розумний дім — кухня". Відображається на платформі і в голосових відповідях. |
| 4 | **Часовий пояс** | Вибір зі списку або автовизначення за IP. |
| 5 | **Голосова модель STT** | Whisper tiny (швидко, Pi 4) / base (баланс) / small (якість, Pi 5). Завантажується. |
| 6 | **Голос TTS (Piper)** | Список голосів для обраної мови. Кнопка "Прослухати". Завантажується ~50 MB. |
| 7 | **Перший користувач** | Ім'я admin, PIN 4–8 цифр. Опціонально: голосовий зліпок (5 фраз). |
| 8 | **Реєстрація на платформі** | QR або посилання. Опціонально — можна пропустити, працює повністю локально. |
| 9 | **Імпорт (опціонально)** | Home Assistant / Tuya / Philips Hue. OAuth за посиланням. Можна пропустити. |

### 5.4 Екран "Що далі" після wizard

- Підключи пристрої → `/discovery` (сканер мережі)
- Встанови модулі → `/modules/install` (маркетплейс)
- Налаштуй голосового асистента → `/settings/voice`
- Додай застосунок на домашній екран → кнопка "Встановити PWA"
- Документація і відео → `docs.smarthome-lk.com`

---

## 6. ОС і режими UI

### 6.1 Рекомендовані операційні системи

| ОС | RAM idle | Рекомендація |
|---|---|---|
| **Raspberry Pi OS Lite** | ~150 MB | ✅ Рекомендується. Офіційна, найкраща підтримка заліза Pi. |
| **DietPi** | ~90 MB | ✅ Рекомендується. Мінімалістична, вбудований інсталятор Docker. |
| Armbian | ~170 MB | Для сторонніх SBC (Orange Pi, NanoPi, Rock Pi). |
| Ubuntu Server 24.04 | ~240 MB | Альтернатива якщо потрібна Ubuntu-екосистема. |
| Raspberry Pi OS Desktop | ~500 MB | ⚠️ Тільки якщо потрібен робочий стіл. Втрачаємо ~350 MB. |

### 6.2 Автодетект режиму UI при старті

Веб-сервер `:8080` працює завжди у всіх режимах. Локальний екран — додатковий клієнт.

| Режим | Умова | Опис |
|---|---|---|
| `HEADLESS` | Немає HDMI | Тільки веб-сервер. Доступ: smarthome.local:8080 + Tailscale. |
| `KIOSK` | X11/Wayland + HDMI | `chromium --kiosk http://localhost:8080` поверх робочого столу. |
| `FRAMEBUFFER` | Lite OS + HDMI + Chromium | `chromium --ozone-platform=drm` без X11, прямо у framebuffer. |
| `TTY` | Lite OS + HDMI, немає Chromium | Python Textual TUI (~15 MB) на TTY1. Статус + навігація. |

Алгоритм автодетекту (`core/ui_detector.py`):

```python
def detect_display_mode() -> str:
    # 1. Чи є X11/Wayland?
    if os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'):
        return 'kiosk'
    # 2. Чи підключений HDMI?
    hdmi = Path('/sys/class/drm').glob('*/status')
    if any('connected' in p.read_text() for p in hdmi):
        if shutil.which('chromium-browser'):
            return 'framebuffer'
        return 'tty'
    # 3. Немає дисплея
    return 'headless'
```

### 6.3 PWA (Progressive Web App)

- `manifest.json` + Service Worker: ui-core підтримує встановлення як PWA
- **Офлайн-сторінка**: при відсутності зв'язку з Pi показує останній стан з кешу
- **Іконка** на домашньому екрані телефону: нативний вигляд без браузерного chrome
- **Web Push VAPID**: сповіщення на телефон навіть коли браузер закритий (через `notify-push`)

### 6.4 Конфігурація UI (core.yaml)

```yaml
ui:
  web_port: 8080
  display_mode: auto        # auto | headless | kiosk | framebuffer | tty
  mdns_announce: true       # smarthome.local
  tty_device: /dev/tty1
  framebuffer: /dev/fb0
  https: true               # самопідписаний сертифікат
```

---

## 7. Голосовий асистент і LLM

### 7.1 Компоненти voice-core

| Компонент | Стек | Характеристики |
|---|---|---|
| Wake-word | openWakeWord | < 5% CPU, постійно у фоні, налаштовуване слово пробудження |
| STT | Whisper.cpp tiny/base/small | Обирається у wizard. Тільки локально, без інтернету. |
| TTS | Piper нейронний | Вибір голосу у wizard з прослуховуванням. Офлайн. Затримка ~300ms. |
| Speaker ID | resemblyzer | Реєстрація: 5 фраз → 256-float d-vector у `/secure/biometrics/` |
| Режим приватності | GPIO + голос | Фіз. кнопка GPIO **АБО** команда "Дім, тихо" → мікрофон вимкнений |

### 7.2 Pipeline голосового запиту

```
openWakeWord → чує wake-word
      ↓
Запис аудіо (до паузи 1.5 сек)
      ↓
Whisper.cpp → текст запиту              ~0.8–2 сек
      ↓
Speaker ID: хто говорить?               ~200 мс
      ↓
Intent Router — Рівень 1: Fast Matcher  ~50 мс
      ↓ не знайшов
Intent Router — Рівень 2: LLM (Pi 5)   ~3–8 сек
      ↓
Знайшов модуль → Core API → виконання
Не знайшов    → TTS: "Такого модуля немає. Знайти в маркеті?"
      ↓
Piper TTS → відтворення відповіді       ~300 мс
      ↓
Запис в історію діалогів (SQLite)
```

### 7.3 Intent Router — дворівневий

**Рівень 1 — Fast Matcher (< 50мс, працює на Pi 4 і Pi 5)**
- Keyword/regex правила для частих команд
- Конфігурується в YAML: `"увімкни світло" → lights.on`
- Без LLM — миттєво

**Рівень 2 — LLM Intent (3–8 сек, тільки Pi 5 з 8GB RAM)**
- Ollama з моделлю phi-3-mini (3.8B int4) або gemma-2b
- System prompt містить динамічний реєстр встановлених модулів
- Реєстр перебудовується при кожному встановленні/видаленні модуля
- Повертає JSON: `{ intent, module, params, confidence }`
- Якщо `confidence < 0.7` → просить повторити
- Автовимикається якщо вільної RAM < 5GB

### 7.4 Голосовий ввід через браузер клієнта

- `getUserMedia()` → WebSocket → Pi: аудіо стрімиться чанками по 100мс
- Pi: `ffmpeg` → WAV 16kHz → Whisper.cpp → Intent Router → Piper TTS → WAV відповідь
- Нічого не передається в хмару — весь pipeline локально на Pi
- Автодетект мікрофона клієнта: `enumerateDevices()` — якщо немає, кнопка PTT прихована

### 7.5 Мовні налаштування

- Мова інтерфейсу і мова TTS-голосу обираються незалежно
- Підтримувані мови бета: `ru`, `uk`, `en`
- Додавання мови = завантаження мовного пакету Piper (~50 MB) через `/settings/voice`
- System prompt LLM надсилається мовою активного користувача

### 7.6 Біометрія — абсолютне обмеження

> **Голосові зліпки (d-vector) і face embeddings зберігаються ТІЛЬКИ у `/secure/biometrics/` на пристрої. Синхронізація в хмару заблокована на рівні ядра. Це обмеження не конфігурується і не знімається жодними командами платформи.**

---

## 8. Аудіо-підсистема

### 8.1 Джерела мікрофона (пріоритет автодетекту)

| Тип | Інтерфейс | Особливості |
|---|---|---|
| USB-мікрофон | USB | Plug&play. Пріоритет 1. |
| ReSpeaker HAT | I2C/SPI | Багатоканальний. Потребує `seeed-voicecard`. Пріоритет 2. |
| I2S GPIO (INMP441, SPH0645) | GPIO 18–21 | `dtoverlay` у `/boot/config.txt`. Пріоритет 3. |
| Bluetooth | PulseAudio + bluez | Затримка ~150мс. Pairing через ui-core. Пріоритет 4. |
| HDMI (ARC) | HDMI | Рідко використовується. Пріоритет 5. |

### 8.2 Джерела динаміка

| Тип | Інтерфейс | Особливості |
|---|---|---|
| USB звукова карта | USB | Plug&play. Найкраща якість. Пріоритет 1. |
| I2S DAC HAT (HiFiBerry та ін.) | GPIO | `dtoverlay`. Висока якість. Пріоритет 2. |
| Bluetooth-колонка | BT | Pairing через ui-core. MAC зберігається для автопідключення. Пріоритет 3. |
| HDMI (динаміки монітора) | HDMI | Автодетект. Пріоритет 4. |
| 3.5mm jack | Аналог | Вбудований у Pi. Середня якість. Пріоритет 5. |

### 8.3 Конфігурація (core.yaml)

```yaml
audio:
  input_priority:  [usb, i2s_gpio, bluetooth, hdmi, builtin]
  output_priority: [usb, i2s_gpio, bluetooth, hdmi, jack]
  force_input:  null          # або "hw:2,0" для перевизначення
  force_output: null          # або "bluez_sink.AA_BB_CC"
  i2s_overlay:  null          # "googlevoicehat" | "hifiberry-dacplus" | ...
  bluetooth_sink: null        # MAC адреса BT-динаміка після pairing
```

### 8.4 Сторінка /settings/audio у ui-core

- Список знайдених пристроїв з рівнем сигналу в реальному часі
- Кнопка "Тест мікрофона" — запис 3 сек + відтворення
- Кнопка "Тест динаміка" — Piper вимовляє тестову фразу
- Bluetooth: "Додати пристрій" → scan 30 сек → вибір зі списку → pairing
- Bluetooth pairing flow: `bluetoothctl pair MAC → trust MAC → connect MAC`

---

## 9. Користувачі, авторизація, аудит

### 9.1 Ролі і права

| Дія | admin | resident | guest |
|---|---|---|---|
| Керування пристроями | Повне | Повне | Тільки читання |
| Встановлення/видалення модулів | Так | Ні | Ні |
| Налаштування ядра і wizard | Так | Ні | Ні |
| Голосові команди | Всі | Всі | Обмежені |
| Перегляд аудит-логу | Так (всі) | Тільки свої | Ні |
| OAuth авторизація інтеграцій | Так | Ні | Ні |
| Керування Tailscale | Так | Ні | Ні |

### 9.2 Способи авторизації у ui-core

- **PIN** (4–8 цифр) — завжди доступний
- **Face ID** — якщо зареєстровано і клієнт має камеру. Браузер захоплює JPEG кадр → POST → Pi face_recognition → JWT сесія. Фото не зберігається.
- **Голосовий зліпок** — ідентифікація при голосовому запиті (персоналізація команд, не вхід у UI)

> **HTTPS обов'язковий** для `getUserMedia()`. Без нього браузер не дає доступ до камери і мікрофона. Самопідписаний сертифікат генерується автоматично при ініціалізації.

### 9.3 Модель користувача (SQLite)

```sql
user_id        TEXT PRIMARY KEY   -- uuid4
name           TEXT               -- відображуване ім'я
role           TEXT               -- admin | resident | guest
pin_hash       TEXT               -- SHA256 PIN
voice_enrolled BOOLEAN
face_enrolled  BOOLEAN
lang           TEXT               -- ru | uk | en
created_at     REAL               -- unix timestamp
```

### 9.4 Аудит-лог

- Зберігається локально у SQLite. Доступний тільки `admin`.
- Що логується: вхід/вихід, голосові команди (текст запиту), зміни налаштувань, встановлення/видалення модулів, керування пристроями.
- Ротація: останні 10 000 записів.
- Сторінка `/settings/audit` у ui-core: таблиця з фільтрами за користувачем, дією, датою.

---

## 10. Мережа, безпека, віддалений доступ

### 10.1 Tailscale — віддалений доступ з інтернету

Tailscale встановлюється як системний модуль `remote-access`. Створює зашифрований WireGuard-тунель без відкритих портів на роутері.

- Налаштування: у wizard (крок 8) або `/settings/remote` — QR-код → `tailscale.com` → авторизація
- Після підключення Pi доступний за адресою `100.x.x.x` або через MagicDNS (`smarthome-kitchen.ts.net`)
- Безкоштовний план Tailscale: до 100 пристроїв, без обмежень трафіку
- Статус: `/settings/remote` → "Підключено / Відключено / Помилка"

### 10.2 Firewall — правила iptables

```bash
# Core API — тільки localhost і core_net
iptables -A INPUT -p tcp --dport 7070 -s 127.0.0.1 -j ACCEPT
iptables -A INPUT -p tcp --dport 7070 -j DROP

# Веб-інтерфейс — локальна мережа + Tailscale
iptables -A INPUT -p tcp --dport 8080 -s 192.168.0.0/16 -j ACCEPT
iptables -A INPUT -p tcp --dport 8080 -s 100.0.0.0/8 -j ACCEPT  # Tailscale
iptables -A INPUT -p tcp --dport 8080 -j DROP
```

Розділ `/secure` не доступний модулям — volume-маунт `/secure` у контейнері `smarthome-modules` відсутній.

### 10.3 Rate limiting

| Дія | Ліміт | Наслідок |
|---|---|---|
| Невірний PIN | 5 спроб / 60 сек | Блокування 10 хвилин, запис у аудит-лог |
| Core API запити | 100 / сек на токен | HTTP 429 |
| WebSocket аудіо (STT) | 1 сесія на користувача | Відхилення нового підключення |

### 10.4 HTTPS і сертифікати

- Автоматично генерується самопідписаний сертифікат (mkcert) при ініціалізації
- Видається на `smarthome.local`, `smarthome-setup.local` і IP-адресу пристрою
- Користувач може завантажити власний сертифікат через `/settings/security`
- Без HTTPS — `getUserMedia()` недоступний. Це блокуюча вимога для голосу і Face ID.

---

## 11. Сповіщення

### 11.1 Канали доставки

| Канал | Коли працює | Реалізація |
|---|---|---|
| TTS голосовий | Завжди (Pi вдома) | Piper → ALSA/BT. Пріоритет: критичні алерти. |
| SSE у браузер | Поки браузер відкритий | EventSource у ui-core. Real-time статус. |
| Web Push VAPID | Браузер закритий, телефон онлайн | Service Worker на телефоні. Модуль `notify-push`. |
| Telegram-бот | Telegram встановлений | Модуль `INTEGRATION`. Авторизація через Bot API. |

### 11.2 Пріоритети сповіщень

| Рівень | Приклади | Канали |
|---|---|---|
| `CRITICAL` | Пожежний датчик, протікання, злам | TTS негайно + Push + Telegram |
| `HIGH` | Батарея < 10%, перегрів Pi | Push + Telegram |
| `NORMAL` | Задачу виконано, модуль оновлено | SSE у браузер |
| `INFO` | Світло вимкнено, двері зачинено | Тільки в історії (не push) |

---

## 12. Імпорт з існуючих систем

### 12.1 Підтримувані системи (бета)

| Система | Авторизація | Що імпортується |
|---|---|---|
| **Home Assistant** | OAuth2 + URL сервера | Пристрої, кімнати, автоматизації (прості), сцени |
| **Tuya / SmartLife** | OAuth2 за посиланням → QR у застосунку | Пристрої, кімнати, DP-коди команд |
| **Philips Hue** | Натиснути кнопку на Bridge (локально) | Лампи, групи, сцени. Без інтернету. |
| Samsung SmartThings | OAuth2 smartthings.com | Пристрої, кімнати |
| IKEA TRÅDFRI | PSK автогенерація | Лампи, групи, штори |
| MQTT Broker | host + login + password | Топіки як пристрої |

### 12.2 Процес імпорту (4 кроки у ui-core)

1. **Вибір джерела** — картки з логотипами, badge "Популярне" на перших трьох
2. **Авторизація** — OAuth: кнопка → redirect → callback. Кнопка Bridge: таймер 30 сек. PSK: форма.
3. **Попередній перегляд** — чекбокси за групами: Освітлення / Клімат / Безпека / Автоматизації
4. **Прогрес** — SSE stream: "Імпортую 12/20...", "Конвертую автоматизації 3/8 (5 потребують доопрацювання)"

### 12.3 Модулі-мости

Після імпорту пристрої керуються через модуль-міст — транслює команди Core API назад у вихідну систему з двосторонньою синхронізацією станів.

- `ha-bridge` — WebSocket sync з Home Assistant у реальному часі
- `tuya-bridge` — Tuya Open API + push через Tuya MQTT

> **Автоматизації:** прості (`якщо X → Y`) конвертуються повністю. Складні (шаблони Jinja2, скрипти) → заготовки з позначкою "потребує доопрацювання".

### 12.4 Формат конвертації → Device Registry

```json
{
  "device_id": "uuid-auto",
  "name": "Світло у вітальні",
  "type": "actuator",
  "protocol": "home_assistant",
  "state": { "on": true, "brightness": 80 },
  "capabilities": ["turn_on", "turn_off", "set_brightness"],
  "meta": {
    "import_source": "home_assistant",
    "ha_entity_id": "light.living_room",
    "ha_area": "Вітальня",
    "imported_at": "2026-03-20T10:00:00Z"
  },
  "module_id": "ha-bridge"
}
```

---

## 13. Моніторинг ресурсів і деградація

### 13.1 hw-monitor — системний модуль

- Кожні 30 сек: температура CPU (`/sys/class/thermal`), RAM (`free`), диск (`df`), uptime
- Дані включаються у heartbeat ping на платформу SmartHome LK
- Графіки за останні 24 години на сторінці `/settings/system` у ui-core

### 13.2 Пороги і автоматичні реакції

| Метрика | Поріг | Дія |
|---|---|---|
| Температура CPU | > 80°C | ⚠️ WARN алерт користувачу + сповіщення на платформу |
| Температура CPU | > 90°C | 🔴 Зупинити LLM Engine + CRITICAL алерт |
| RAM вільна | < 300 MB | Заборона встановлення нових модулів |
| RAM вільна | < 150 MB | Стоп AUTOMATION → стоп INTEGRATION → попередження |
| Диск вільний | < 500 MB | Попередження |
| Диск вільний | < 100 MB | Стоп бекапу |

### 13.3 Стратегія деградації при нестачі RAM

1. Попередження користувачу у ui-core + заборона встановлення нових модулів
2. При RAM < 150 MB: автостоп за пріоритетом — спочатку AUTOMATION, потім INTEGRATION
3. UI-модулі і DRIVER-модулі — тільки при явному дозволі користувача
4. SYSTEM-модулі не зупиняються (виняток: LLM Engine при перегріві CPU > 90°C)

---

## 14. Python SDK для розробників модулів

### 14.1 Встановлення

```bash
pip install smarthome-sdk
```

### 14.2 Базовий клас модуля

```python
from smarthome_sdk import SmartHomeModule, on_event, schedule

class MyModule(SmartHomeModule):
    name = "my-climate-module"
    version = "1.0.0"

    async def on_start(self):
        self.logger.info("Module started")

    @on_event("device.state_changed")
    async def handle_state(self, event):
        device = await self.devices.get(event.device_id)
        if device.state.get("temperature") > 25:
            await self.devices.set_state(device.id, {"fan": True})

    @schedule("*/5 * * * *")
    async def periodic_check(self):
        devices = await self.devices.list(type="sensor")

    async def on_stop(self):
        pass  # graceful shutdown
```

### 14.3 CLI-команди

```bash
smarthome new-module my-integration   # створити структуру модуля
smarthome dev                         # запустити mock Core API на :7070
smarthome test my-module.zip          # sandbox-тест
smarthome publish                     # відправити в маркетплейс
```

### 14.4 Структура нового модуля (scaffold)

```
my-integration/
  manifest.json
  main.py
  test_module.py
  widget.html          # якщо ui_profile != HEADLESS
  settings.html        # якщо ui_profile != HEADLESS
  icon.svg
  Dockerfile
  README.md
```

### 14.5 Mock Core API для локальної розробки

```bash
smarthome dev
# Запускає mock сервер на localhost:7070
# Підтримує всі ендпоінти Core API v1
# Попередньо заповнений тестовими пристроями
# Логує всі запити у консоль
```

### 14.6 Документація API

- Swagger UI: `http://smarthome.local:7070/docs` (генерується FastAPI автоматично)
- Публічна документація: `docs.smarthome-lk.com/module-sdk`

---

## 15. Офлайн-режим

> **Базовий сценарій "керування домом голосом і через UI" працює повністю без інтернету. Хмара — опціональне розширення, не обов'язкова залежність.**

| Функція | Без інтернету | Примітка |
|---|---|---|
| Голосовий асистент (STT/TTS) | ✅ Так | Whisper + Piper — повністю локально |
| LLM Intent Router | ✅ Так | Ollama локально на Pi 5 |
| Device Registry | ✅ Так | SQLite локально |
| Автоматизації | ✅ Так | Локальні пристрої |
| Веб-інтерфейс :8080 | ✅ Так | Локальна мережа |
| Історія діалогів | ✅ Так | SQLite локально |
| Tailscale (віддалений доступ) | ❌ Ні | Потребує інтернет для тунелю |
| Cloud Sync з платформою | ⚠️ Частково | Буферизує, відправить при відновленні |
| OAuth-інтеграції (Gmail, Tuya) | ❌ Ні | Хмарно-залежні сервіси |
| Оновлення модулів з маркету | ❌ Ні | Потребує інтернет |
| Web Push сповіщення | ❌ Ні | FCM потребує інтернет |

---

## 16. Критерії готовності v0.3 (Definition of Done)

### 16.1 Onboarding

- [ ] Готовий .img образ записується на SD і завантажується без додаткових налаштувань
- [ ] Pi піднімає AP `SmartHome-Setup` при першому старті. QR на HDMI якщо підключений.
- [ ] Wizard проходить всі 9 кроків у браузері телефону без помилок
- [ ] Після wizard показується екран "Що далі" з трьома рекомендаціями

### 16.2 Ядро і модулі

- [ ] 2-контейнерна схема працює. Sandbox-контейнер автовидаляється після тесту.
- [ ] Збій одного модуля не зупиняє решту (тест: `kill -9` потоку модуля)
- [ ] Watchdog: systemd + Docker автоматично піднімають впалі контейнери
- [ ] Integrity Agent виявляє зміну файлів ядра за ≤ 30 сек

### 16.3 Голос і LLM

- [ ] STT працює без інтернету (тест: `ip link set eth0 down` → команда розпізнається)
- [ ] TTS вимовляє відповідь локально через Piper
- [ ] Режим приватності: GPIO кнопка І голосова команда вимикають мікрофон
- [ ] Fast Matcher обробляє зареєстровані команди за < 50мс
- [ ] Біометрія відсутня у будь-яких вихідних HTTP-запитах (тест через `tcpdump`)

### 16.4 UI і доступ

- [ ] PWA встановлюється на домашній екран телефону. Офлайн-сторінка показує кеш.
- [ ] Tailscale тунель налаштовується через ui-core. Pi доступний через MagicDNS.
- [ ] Всі 4 режими UI (HEADLESS/KIOSK/FRAMEBUFFER/TTY) працюють коректно
- [ ] HTTPS: самопідписаний сертифікат, `getUserMedia()` доступний

### 16.5 Безпека

- [ ] Core API :7070 недоступний ззовні localhost (тест через зовнішній IP)
- [ ] 5 невірних PIN → блокування 10 хвилин, запис у аудит-лог
- [ ] Аудит-лог зберігає дії. Доступний тільки `admin`.
- [ ] Деградація RAM: AUTOMATION зупиняється при < 150 MB вільної RAM

### 16.6 SDK і імпорт

- [ ] `smarthome new-module` створює робочу структуру
- [ ] `smarthome dev` запускає mock Core API локально
- [ ] Імпорт з Home Assistant: пристрої і прості автоматизації
- [ ] OAuth QR-flow завершується успішно для Tuya і Home Assistant

---

## 17. Out of Scope — за межами бети

| Що не входить | Планується |
|---|---|
| GPG-підписання образу ядра | v0.4 |
| Мультихаб (кластер з кількох Pi) | v0.5 |
| Вбудований Video Doorbell (відеодзвінок) | v0.4 |
| OTA оновлення за розкладом без команди платформи | v0.5 |
| UPS / резервне живлення | Community модуль |
| Моніторинг Prometheus/Grafana | Community модуль |
| Z-Wave нативно у ядрі | v0.4 (тільки через DRIVER-модуль) |
| Apple HomeKit нативно | v0.5 |
| Мобільний застосунок (iOS/Android native) | v1.0 |

---

*SmartHome LK · Core TZ v0.3.0-beta · 2026-03-20 · Open Source / MIT*
