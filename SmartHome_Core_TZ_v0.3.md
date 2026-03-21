# SmartHome LK Core — Техническое задание v0.3-beta
**Дата:** 2026-03-20 | **Лицензия:** Open Source (MIT) | **Статус:** DRAFT

---

## Содержание

1. Введение и концепция
2. Архитектура: 2-контейнерная схема
3. Классификация модулей
4. Системные модули ядра
5. Первый запуск — Onboarding Wizard
6. ОС и режимы UI
7. Голосовой ассистент и LLM
8. Аудио-подсистема
9. Пользователи, авторизация, аудит
10. Сеть, безопасность, удалённый доступ
11. Уведомления
12. Импорт из существующих систем
13. Мониторинг ресурсов и деградация
14. Python SDK для разработчиков модулей
15. Оффлайн-режим
16. Критерии готовности (Definition of Done)
17. Out of Scope

---

## 1. Введение и концепция

SmartHome LK Core — открытый (Open Source, MIT) локальный хаб умного дома. Устанавливается на Raspberry Pi 4/5 или любой SBC под управлением Linux. Не требует подписки для базовой работы. Интегрируется с платформой SmartHome LK для облачных функций, маркетплейса модулей и удалённого управления.

### 1.1 Три фундаментальных принципа

**Ядро неизменно** — файлы ядра защищены SHA256-эталоном и флагом `chattr +i`. Изменение невозможно без явного обновления через официальный канал платформы.

**Модули изолированы** — вся сторонняя логика выполняется в изолированных Python-потоках внутри контейнера модулей. Общение с ядром только через Core API (HTTP, localhost:7070). Прямой доступ к данным ядра и разделу `/secure` запрещён.

**Агент наблюдает** — независимый процесс `IntegrityAgent` непрерывно проверяет SHA256-хеши файлов ядра и реагирует по цепочке: стоп модулей → уведомление платформы → откат из резервной копии → SAFE MODE.

### 1.2 Open Source

Проект распространяется под лицензией MIT. UPS/резервное питание, кастомные аппаратные конфигурации — на усмотрение сообщества. ТЗ фиксирует базовую функциональность ядра.

---

## 2. Архитектура: 2-контейнерная схема

Вместо отдельного Docker-контейнера на каждый модуль используется минимальная схема.

| Контейнер | RAM | Назначение |
|---|---|---|
| `smarthome-core` | ~420 MB | Ядро: FastAPI, Device Registry, Event Bus, Module Loader, Cloud Sync, Voice Core, LLM Engine, UI Core |
| `smarthome-modules` | 180–350 MB | Все пользовательские модули в одном Python-процессе через Plugin Manager |
| `smarthome-sandbox` | 96–256 MB, `--rm` | Временный: тест нового модуля перед установкой. Автоудаляется. |

### 2.1 Plugin Manager

Plugin Manager — компонент ядра, загружающий модули как Python-классы через `importlib` в изолированный namespace с отдельным потоком (Thread).

- Краш модуля (Exception) → перехватывается, логируется, перезапускается только этот модуль
- OOM / segfault → падает контейнер `smarthome-modules` целиком → systemd автоматически поднимает
- Hot-reload: обновление модуля через `importlib.reload()` без перезапуска контейнера
- Лимит памяти на модуль: `resource.setrlimit` + `tracemalloc` мониторинг внутри потока

### 2.2 Watchdog — двухуровневая защита

- **Уровень 1 — systemd**: `smarthome-core.service` и `smarthome-modules.service` с `Restart=always`, `RestartSec=5s`
- **Уровень 2 — Docker**: `--restart=unless-stopped` на обоих контейнерах
- **Integrity Agent**: отдельный `smarthome-agent.service`, независим от обоих контейнеров

### 2.3 Экономия памяти vs отдельные контейнеры

| Конфигурация | RAM (типичная нагрузка) |
|---|---|
| По контейнеру на каждый модуль (8 модулей) | ~1 200 MB |
| 2-контейнерная схема (те же 8 модулей) | ~620 MB |
| Экономия | ~580 MB (−48%) |

---

## 3. Классификация модулей

### 3.1 Типы модулей

| Тип | Удалить? | Описание |
|---|---|---|
| `SYSTEM` | Нельзя | Поставляется с ядром. Расширенные привилегии. Запускается в процессе ядра. |
| `UI` | Можно | Иконка в меню + виджет на дашборде + страница настроек. iframe sandbox. |
| `INTEGRATION` | Можно | Внешние сервисы через OAuth/API. Токены в ядре, модуль не видит их напрямую. |
| `DRIVER` | Можно | Драйвер протокола: Zigbee, Z-Wave, MQTT, HTTP-устройства. |
| `AUTOMATION` | Можно | Сценарии без UI. Event listeners + scheduler. Самые лёгкие (~40 MB). |
| `IMPORT_SOURCE` | Можно | Импорт из Home Assistant, Tuya, Philips Hue и других систем. |

### 3.2 UI-профили

| Профиль | Компоненты | Пример |
|---|---|---|
| `HEADLESS` | Нет UI | Ночной режим, alarm |
| `SETTINGS_ONLY` | Страница настроек | Системный модуль voice-core |
| `ICON_SETTINGS` | Иконка + настройки | Gmail-интеграция |
| `FULL` | Иконка + виджет + настройки | Модуль климата, освещения |

### 3.3 Runtime-режимы (manifest.json)

- `always_on` — запущен постоянно. UI-модули, драйверы, Telegram-уведомления.
- `on_demand` — поток стартует за ~50 мс, выполняет задачу, останавливается. AUTOMATION.
- `scheduled` — cron-строка в manifest. Пример: `"*/5 * * * *"` для проверки Gmail каждые 5 минут.

### 3.4 manifest.json — структура

```yaml
name:          my-module          # уникальное имя (snake_case)
version:       1.0.0              # semver
type:          INTEGRATION        # тип модуля
ui_profile:    ICON_SETTINGS      # UI-профиль
api_version:   "1.0"
runtime_mode:  scheduled
schedule:      "*/5 * * * *"
permissions:
  - device.read
  - events.subscribe
port:          8100               # порт HTTP-сервера модуля

# Если ui_profile != HEADLESS:
ui:
  icon:     icon.svg
  widget:
    file:   widget.html
    size:   "2x1"                 # 1x1 | 2x1 | 2x2 | 4x1
  settings: settings.html

# Если тип INTEGRATION:
oauth:
  provider: google                # google | telegram | custom
  scopes:
    - gmail.readonly
```

### 3.5 Безопасность UI-компонентов

- Все виджеты и страницы настроек рендерятся в `<iframe sandbox>` — модуль не имеет доступа к DOM ядра
- Общение только через `window.postMessage` с whitelist разрешённых типов сообщений
- CSP заголовок: `default-src 'self'` — запрет inline scripts

---

## 4. Системные модули ядра

| Модуль | UI-профиль | Функция |
|---|---|---|
| `voice-core` | SETTINGS_ONLY | STT (Whisper.cpp), TTS (Piper), wake-word, speaker ID, режим приватности |
| `llm-engine` | SETTINGS_ONLY | Ollama, Intent Router (Fast Matcher + LLM), выбор и загрузка моделей |
| `network-scanner` | SETTINGS_ONLY | ARP sweep, mDNS, SSDP/UPnP, Zigbee/Z-Wave, OUI классификация |
| `user-manager` | SETTINGS_ONLY | Профили, роли, голосовые слепки, видеоавторизация, аудит-лог |
| `secrets-vault` | HEADLESS | AES-256-GCM хранилище OAuth-токенов, proxy для модулей |
| `backup-manager` | SETTINGS_ONLY | Локальный бэкап (USB/SD) + облако E2E, QR-перенос секретов |
| `remote-access` | HEADLESS | Tailscale VPN клиент: автоподключение, статус туннеля |
| `hw-monitor` | HEADLESS | Температура CPU, RAM, диск. Алерт + автоснижение нагрузки при перегреве |
| `notify-push` | HEADLESS | Web Push VAPID — уведомления на телефон когда браузер закрыт |
| `ui-core` | FULL | PWA · smarthome.local:8080 · TTY1/kiosk · wizard первого запуска |

---

## 5. Первый запуск — Onboarding Wizard

Цель: пользователь без технических знаний настраивает систему за 5–10 минут используя только телефон.

### 5.1 Шаг 0 — До включения: запись образа на SD

- Готовый `.img` образ (SmartHome LK OS Lite) скачивается с сайта платформы
- Записывается через Raspberry Pi Imager или balenaEtcher — без дополнительных настроек
- Образ: Raspberry Pi OS Lite + Docker + smarthome-core предустановлены

### 5.2 Шаг 1 — Первое включение: точка доступа + QR

При первом старте (или если Wi-Fi не настроен) ядро поднимает точку доступа:

```
SSID:     SmartHome-Setup
Password: smarthome
```

**Если подключён HDMI-дисплей:**
→ QR-код отображается на TTY1
→ Сканируй → попадаешь в wizard в браузере телефона

**Если нет экрана (headless):**
→ Подключись к SmartHome-Setup с телефона
→ Открой браузер → `192.168.4.1`
→ Тот же wizard

mDNS fallback: `http://smarthome-setup.local`

### 5.3 Wizard — 9 шагов в браузере телефона

| # | Шаг | Детали |
|---|---|---|
| 1 | **Язык интерфейса** | Выбор: ru / uk / en. Влияет на все тексты и TTS-голоса. |
| 2 | **Wi-Fi сеть** | Список найденных сетей. Ввод пароля. Pi подключается и проверяет интернет. |
| 3 | **Имя устройства** | Например "Умный дом — кухня". Отображается в платформе и в голосовых ответах. |
| 4 | **Часовой пояс** | Выбор из списка или автоопределение по IP. |
| 5 | **Голосовая модель STT** | Whisper tiny (быстро, Pi 4) / base (баланс) / small (качество, Pi 5). Скачивается. |
| 6 | **Голос TTS (Piper)** | Список голосов для выбранного языка. Кнопка "Прослушать". Скачивается ~50 MB. |
| 7 | **Первый пользователь** | Имя admin, PIN 4–8 цифр. Опционально: голосовой слепок (5 фраз). |
| 8 | **Регистрация на платформе** | QR или ссылка. Опционально — можно пропустить, работает полностью локально. |
| 9 | **Импорт (опционально)** | Home Assistant / Tuya / Philips Hue. OAuth по ссылке. Можно пропустить. |

### 5.4 Экран "Что дальше" после wizard

- Подключи устройства → `/discovery` (сканер сети)
- Установи модули → `/modules/install` (маркетплейс)
- Настрой голосового ассистента → `/settings/voice`
- Добавь приложение на домашний экран → кнопка "Установить PWA"
- Документация и видео → `docs.smarthome-lk.com`

---

## 6. ОС и режимы UI

### 6.1 Рекомендуемые операционные системы

| ОС | RAM idle | Рекомендация |
|---|---|---|
| **Raspberry Pi OS Lite** | ~150 MB | ✅ Рекомендуется. Официальная, лучшая поддержка железа Pi. |
| **DietPi** | ~90 MB | ✅ Рекомендуется. Минималистична, встроенный установщик Docker. |
| Armbian | ~170 MB | Для сторонних SBC (Orange Pi, NanoPi, Rock Pi). |
| Ubuntu Server 24.04 | ~240 MB | Альтернатива если нужна Ubuntu-экосистема. |
| Raspberry Pi OS Desktop | ~500 MB | ⚠️ Только если нужен рабочий стол. Теряем ~350 MB. |

### 6.2 Автодетект режима UI при старте

Веб-сервер `:8080` работает всегда во всех режимах. Локальный экран — дополнительный клиент.

| Режим | Условие | Описание |
|---|---|---|
| `HEADLESS` | Нет HDMI | Только веб-сервер. Доступ: smarthome.local:8080 + Tailscale. |
| `KIOSK` | X11/Wayland + HDMI | `chromium --kiosk http://localhost:8080` поверх рабочего стола. |
| `FRAMEBUFFER` | Lite OS + HDMI + Chromium | `chromium --ozone-platform=drm` без X11, прямо в framebuffer. |
| `TTY` | Lite OS + HDMI, нет Chromium | Python Textual TUI (~15 MB) на TTY1. Статус + навигация. |

Алгоритм автодетекта (`core/ui_detector.py`):

```python
def detect_display_mode() -> str:
    # 1. Есть ли X11/Wayland?
    if os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'):
        return 'kiosk'
    # 2. Подключён ли HDMI?
    hdmi = Path('/sys/class/drm').glob('*/status')
    if any('connected' in p.read_text() for p in hdmi):
        if shutil.which('chromium-browser'):
            return 'framebuffer'
        return 'tty'
    # 3. Нет дисплея
    return 'headless'
```

### 6.3 PWA (Progressive Web App)

- `manifest.json` + Service Worker: ui-core поддерживает установку как PWA
- **Offline-страница**: при отсутствии связи с Pi показывает последнее состояние из кеша
- **Иконка** на домашнем экране телефона: нативный вид без браузерного chrome
- **Web Push VAPID**: уведомления на телефон даже когда браузер закрыт (через `notify-push`)

### 6.4 Конфигурация UI (core.yaml)

```yaml
ui:
  web_port: 8080
  display_mode: auto        # auto | headless | kiosk | framebuffer | tty
  mdns_announce: true       # smarthome.local
  tty_device: /dev/tty1
  framebuffer: /dev/fb0
  https: true               # самоподписанный сертификат
```

---

## 7. Голосовой ассистент и LLM

### 7.1 Компоненты voice-core

| Компонент | Стек | Характеристики |
|---|---|---|
| Wake-word | openWakeWord | < 5% CPU, постоянно в фоне, настраиваемое слово пробуждения |
| STT | Whisper.cpp tiny/base/small | Выбирается в wizard. Только локально, без интернета. |
| TTS | Piper нейронный | Выбор голоса в wizard с прослушиванием. Офлайн. Задержка ~300ms. |
| Speaker ID | resemblyzer | Регистрация: 5 фраз → 256-float d-vector в `/secure/biometrics/` |
| Режим приватности | GPIO + голос | Физ. кнопка GPIO **ИЛИ** команда "Дом, тихо" → микрофон выключен |

### 7.2 Pipeline голосового запроса

```
openWakeWord → слышит wake-word
      ↓
Запись аудио (до паузы 1.5 сек)
      ↓
Whisper.cpp → текст запроса           ~0.8–2 сек
      ↓
Speaker ID: кто говорит?              ~200 ms
      ↓
Intent Router — Уровень 1: Fast Matcher  ~50 ms
      ↓ не нашёл
Intent Router — Уровень 2: LLM (Pi 5)   ~3–8 сек
      ↓
Нашёл модуль → Core API → выполнение
Не нашёл    → TTS: "Такого модуля нет. Найти в маркете?"
      ↓
Piper TTS → воспроизведение ответа    ~300 ms
      ↓
Запись в историю диалогов (SQLite)
```

### 7.3 Intent Router — двухуровневый

**Уровень 1 — Fast Matcher (< 50ms, работает на Pi 4 и Pi 5)**
- Keyword/regex правила для частых команд
- Конфигурируется в YAML: `"включи свет" → lights.on`
- Без LLM — мгновенно

**Уровень 2 — LLM Intent (3–8 сек, только Pi 5 с 8GB RAM)**
- Ollama с моделью phi-3-mini (3.8B int4) или gemma-2b
- System prompt содержит динамический реестр установленных модулей
- Реестр пересобирается при каждой установке/удалении модуля
- Возвращает JSON: `{ intent, module, params, confidence }`
- Если `confidence < 0.7` → просит повторить
- Автоотключается если свободной RAM < 5GB

### 7.4 Голосовой ввод через браузер клиента

- `getUserMedia()` → WebSocket → Pi: аудио стримится чанками по 100ms
- Pi: `ffmpeg` → WAV 16kHz → Whisper.cpp → Intent Router → Piper TTS → WAV ответ
- Ничего не уходит в облако — весь pipeline локально на Pi
- Автодетект микрофона клиента: `enumerateDevices()` — если нет, кнопка PTT скрыта

### 7.5 Языковые настройки

- Язык интерфейса и язык TTS-голоса выбираются независимо
- Поддерживаемые языки бета: `ru`, `uk`, `en`
- Добавление языка = загрузка языкового пакета Piper (~50 MB) через `/settings/voice`
- System prompt LLM отправляется на языке активного пользователя

### 7.6 Биометрия — абсолютное ограничение

> **Голосовые слепки (d-vector) и face embeddings хранятся ТОЛЬКО в `/secure/biometrics/` на устройстве. Синхронизация в облако заблокирована на уровне ядра. Это ограничение не конфигурируется и не снимается никакими командами платформы.**

---

## 8. Аудио-подсистема

### 8.1 Источники микрофона (приоритет автодетекта)

| Тип | Интерфейс | Особенности |
|---|---|---|
| USB-микрофон | USB | Plug&play. Приоритет 1. |
| ReSpeaker HAT | I2C/SPI | Многоканальный. Требует `seeed-voicecard`. Приоритет 2. |
| I2S GPIO (INMP441, SPH0645) | GPIO 18–21 | `dtoverlay` в `/boot/config.txt`. Приоритет 3. |
| Bluetooth | PulseAudio + bluez | Задержка ~150ms. Pairing через ui-core. Приоритет 4. |
| HDMI (ARC) | HDMI | Редко используется. Приоритет 5. |

### 8.2 Источники динамика

| Тип | Интерфейс | Особенности |
|---|---|---|
| USB звуковая карта | USB | Plug&play. Лучшее качество. Приоритет 1. |
| I2S DAC HAT (HiFiBerry и др.) | GPIO | `dtoverlay`. Высокое качество. Приоритет 2. |
| Bluetooth-колонка | BT | Pairing через ui-core. MAC сохраняется для автоподключения. Приоритет 3. |
| HDMI (динамики монитора) | HDMI | Автодетект. Приоритет 4. |
| 3.5mm jack | Аналог | Встроен в Pi. Среднее качество. Приоритет 5. |

### 8.3 Конфигурация (core.yaml)

```yaml
audio:
  input_priority:  [usb, i2s_gpio, bluetooth, hdmi, builtin]
  output_priority: [usb, i2s_gpio, bluetooth, hdmi, jack]
  force_input:  null          # или "hw:2,0" для переопределения
  force_output: null          # или "bluez_sink.AA_BB_CC"
  i2s_overlay:  null          # "googlevoicehat" | "hifiberry-dacplus" | ...
  bluetooth_sink: null        # MAC адрес BT-динамика после pairing
```

### 8.4 Страница /settings/audio в ui-core

- Список найденных устройств с уровнем сигнала в реальном времени
- Кнопка "Тест микрофона" — запись 3 сек + воспроизведение
- Кнопка "Тест динамика" — Piper произносит тестовую фразу
- Bluetooth: "Добавить устройство" → scan 30 сек → выбор из списка → pairing
- Bluetooth pairing flow: `bluetoothctl pair MAC → trust MAC → connect MAC`

---

## 9. Пользователи, авторизация, аудит

### 9.1 Роли и права

| Действие | admin | resident | guest |
|---|---|---|---|
| Управление устройствами | Полное | Полное | Только чтение |
| Установка/удаление модулей | Да | Нет | Нет |
| Настройки ядра и wizard | Да | Нет | Нет |
| Голосовые команды | Все | Все | Ограниченные |
| Просмотр аудит-лога | Да (все) | Только свои | Нет |
| OAuth авторизация интеграций | Да | Нет | Нет |
| Tailscale управление | Да | Нет | Нет |

### 9.2 Способы авторизации в ui-core

- **PIN** (4–8 цифр) — всегда доступен
- **Face ID** — если зарегистрировано и клиент имеет камеру. Браузер захватывает JPEG кадр → POST → Pi face_recognition → JWT сессия. Фото не сохраняется.
- **Голосовой слепок** — идентификация при голосовом запросе (персонализация команд, не вход в UI)

> **HTTPS обязателен** для `getUserMedia()`. Без него браузер не дает доступ к камере и микрофону. Самоподписанный сертификат генерируется автоматически при инициализации.

### 9.3 Модель пользователя (SQLite)

```sql
user_id        TEXT PRIMARY KEY   -- uuid4
name           TEXT               -- отображаемое имя
role           TEXT               -- admin | resident | guest
pin_hash       TEXT               -- SHA256 PIN
voice_enrolled BOOLEAN
face_enrolled  BOOLEAN
lang           TEXT               -- ru | uk | en
created_at     REAL               -- unix timestamp
```

### 9.4 Аудит-лог

- Хранится локально в SQLite. Доступен только `admin`.
- Что логируется: вход/выход, голосовые команды (текст запроса), изменения настроек, установка/удаление модулей, управление устройствами.
- Ротация: последние 10 000 записей.
- Страница `/settings/audit` в ui-core: таблица с фильтрами по пользователю, действию, дате.

---

## 10. Сеть, безопасность, удалённый доступ

### 10.1 Tailscale — удалённый доступ из интернета

Tailscale устанавливается как системный модуль `remote-access`. Создаёт зашифрованный WireGuard-туннель без открытых портов на роутере.

- Настройка: в wizard (шаг 8) или `/settings/remote` — QR-код → `tailscale.com` → авторизация
- После подключения Pi доступен по адресу `100.x.x.x` или через MagicDNS (`smarthome-kitchen.ts.net`)
- Бесплатный план Tailscale: до 100 устройств, без ограничений трафика
- Статус: `/settings/remote` → "Подключено / Отключено / Ошибка"

### 10.2 Firewall — правила iptables

```bash
# Core API — только localhost и core_net
iptables -A INPUT -p tcp --dport 7070 -s 127.0.0.1 -j ACCEPT
iptables -A INPUT -p tcp --dport 7070 -j DROP

# Веб-интерфейс — локальная сеть + Tailscale
iptables -A INPUT -p tcp --dport 8080 -s 192.168.0.0/16 -j ACCEPT
iptables -A INPUT -p tcp --dport 8080 -s 100.0.0.0/8 -j ACCEPT  # Tailscale
iptables -A INPUT -p tcp --dport 8080 -j DROP
```

Раздел `/secure` не доступен модулям — volume-маунт `/secure` в контейнере `smarthome-modules` отсутствует.

### 10.3 Rate limiting

| Действие | Лимит | Последствие |
|---|---|---|
| Неверный PIN | 5 попыток / 60 сек | Блокировка 10 минут, запись в аудит-лог |
| Core API запросы | 100 / сек на токен | HTTP 429 |
| WebSocket аудио (STT) | 1 сессия на пользователя | Отклонение нового подключения |

### 10.4 HTTPS и сертификаты

- Автоматически генерируется самоподписанный сертификат (mkcert) при инициализации
- Выдаётся на `smarthome.local`, `smarthome-setup.local` и IP-адрес устройства
- Пользователь может загрузить собственный сертификат через `/settings/security`
- Без HTTPS — `getUserMedia()` недоступен. Это блокирующее требование для голоса и Face ID.

---

## 11. Уведомления

### 11.1 Каналы доставки

| Канал | Когда работает | Реализация |
|---|---|---|
| TTS голосовой | Всегда (Pi дома) | Piper → ALSA/BT. Приоритет: критичные алерты. |
| SSE в браузер | Пока браузер открыт | EventSource в ui-core. Real-time статус. |
| Web Push VAPID | Браузер закрыт, телефон online | Service Worker на телефоне. Модуль `notify-push`. |
| Telegram-бот | Telegram установлен | Модуль `INTEGRATION`. Авторизация через Bot API. |

### 11.2 Приоритеты уведомлений

| Уровень | Примеры | Каналы |
|---|---|---|
| `CRITICAL` | Пожарный датчик, протечка, взлом | TTS немедленно + Push + Telegram |
| `HIGH` | Батарея < 10%, перегрев Pi | Push + Telegram |
| `NORMAL` | Задача выполнена, модуль обновлён | SSE в браузер |
| `INFO` | Свет выключен, дверь закрыта | Только в истории (не push) |

---

## 12. Импорт из существующих систем

### 12.1 Поддерживаемые системы (бета)

| Система | Авторизация | Что импортируется |
|---|---|---|
| **Home Assistant** | OAuth2 + URL сервера | Устройства, комнаты, автоматизации (простые), сцены |
| **Tuya / SmartLife** | OAuth2 по ссылке → QR в приложении | Устройства, комнаты, DP-коды команд |
| **Philips Hue** | Нажать кнопку на Bridge (локально) | Лампы, группы, сцены. Без интернета. |
| Samsung SmartThings | OAuth2 smartthings.com | Устройства, комнаты |
| IKEA TRÅDFRI | PSK автогенерация | Лампы, группы, шторы |
| MQTT Broker | host + login + password | Топики как устройства |

### 12.2 Процесс импорта (4 шага в ui-core)

1. **Выбор источника** — карточки с логотипами, badge "Популярное" на первых трёх
2. **Авторизация** — OAuth: кнопка → redirect → callback. Кнопка Bridge: таймер 30 сек. PSK: форма.
3. **Предпросмотр** — чекбоксы по группам: Освещение / Климат / Безопасность / Автоматизации
4. **Прогресс** — SSE stream: "Импортирую 12/20...", "Конвертирую автоматизации 3/8 (5 требуют доработки)"

### 12.3 Модули-мосты

После импорта устройства управляются через модуль-мост — транслирует команды Core API обратно в исходную систему с двусторонней синхронизацией состояний.

- `ha-bridge` — WebSocket sync с Home Assistant в реальном времени
- `tuya-bridge` — Tuya Open API + push через Tuya MQTT

> **Автоматизации:** простые (`если X → Y`) конвертируются полностью. Сложные (шаблоны Jinja2, скрипты) → заготовки с пометкой "требует доработки".

### 12.4 Формат конвертации → Device Registry

```json
{
  "device_id": "uuid-auto",
  "name": "Свет в гостиной",
  "type": "actuator",
  "protocol": "home_assistant",
  "state": { "on": true, "brightness": 80 },
  "capabilities": ["turn_on", "turn_off", "set_brightness"],
  "meta": {
    "import_source": "home_assistant",
    "ha_entity_id": "light.living_room",
    "ha_area": "Гостиная",
    "imported_at": "2026-03-20T10:00:00Z"
  },
  "module_id": "ha-bridge"
}
```

---

## 13. Мониторинг ресурсов и деградация

### 13.1 hw-monitor — системный модуль

- Каждые 30 сек: температура CPU (`/sys/class/thermal`), RAM (`free`), диск (`df`), uptime
- Данные включаются в heartbeat ping на платформу SmartHome LK
- Графики за последние 24 часа на странице `/settings/system` в ui-core

### 13.2 Пороги и автоматические реакции

| Метрика | Порог | Действие |
|---|---|---|
| Температура CPU | > 80°C | ⚠️ WARN алерт пользователю + уведомление на платформу |
| Температура CPU | > 90°C | 🔴 Остановить LLM Engine + CRITICAL алерт |
| RAM свободная | < 300 MB | Запрет установки новых модулей |
| RAM свободная | < 150 MB | Стоп AUTOMATION → стоп INTEGRATION → предупреждение |
| Диск свободный | < 500 MB | Предупреждение |
| Диск свободный | < 100 MB | Стоп бэкапа |

### 13.3 Стратегия деградации при нехватке RAM

1. Предупреждение пользователю в ui-core + запрет установки новых модулей
2. При RAM < 150 MB: автостоп по приоритету — сначала AUTOMATION, затем INTEGRATION
3. UI-модули и DRIVER-модули — только при явном разрешении пользователя
4. SYSTEM-модули не останавливаются (исключение: LLM Engine при перегреве CPU > 90°C)

---

## 14. Python SDK для разработчиков модулей

### 14.1 Установка

```bash
pip install smarthome-sdk
```

### 14.2 Базовый класс модуля

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

### 14.3 CLI-команды

```bash
smarthome new-module my-integration   # создать структуру модуля
smarthome dev                         # запустить mock Core API на :7070
smarthome test my-module.zip          # sandbox-тест
smarthome publish                     # отправить в маркетплейс
```

### 14.4 Структура нового модуля (scaffold)

```
my-integration/
  manifest.json
  main.py
  test_module.py
  widget.html          # если ui_profile != HEADLESS
  settings.html        # если ui_profile != HEADLESS
  icon.svg
  Dockerfile
  README.md
```

### 14.5 Mock Core API для локальной разработки

```bash
smarthome dev
# Запускает mock сервер на localhost:7070
# Поддерживает все эндпоинты Core API v1
# Предзаполнен тестовыми устройствами
# Логирует все запросы в консоль
```

### 14.6 Документация API

- Swagger UI: `http://smarthome.local:7070/docs` (генерируется FastAPI автоматически)
- Публичная документация: `docs.smarthome-lk.com/module-sdk`

---

## 15. Оффлайн-режим

> **Базовый сценарий "управление домом голосом и через UI" работает полностью без интернета. Облако — опциональное расширение, не обязательная зависимость.**

| Функция | Без интернета | Примечание |
|---|---|---|
| Голосовой ассистент (STT/TTS) | ✅ Да | Whisper + Piper — полностью локально |
| LLM Intent Router | ✅ Да | Ollama локально на Pi 5 |
| Device Registry | ✅ Да | SQLite локально |
| Автоматизации | ✅ Да | Локальные устройства |
| Веб-интерфейс :8080 | ✅ Да | Локальная сеть |
| История диалогов | ✅ Да | SQLite локально |
| Tailscale (удалённый доступ) | ❌ Нет | Требует интернет для туннеля |
| Cloud Sync с платформой | ⚠️ Частично | Буферизует, отправит при восстановлении |
| OAuth-интеграции (Gmail, Tuya) | ❌ Нет | Cloud-зависимые сервисы |
| Обновление модулей из маркета | ❌ Нет | Требует интернет |
| Web Push уведомления | ❌ Нет | FCM требует интернет |

---

## 16. Критерии готовности v0.3 (Definition of Done)

### 16.1 Onboarding

- [ ] Готовый .img образ записывается на SD и загружается без дополнительных настроек
- [ ] Pi поднимает AP `SmartHome-Setup` при первом старте. QR на HDMI если подключён.
- [ ] Wizard проходит все 9 шагов в браузере телефона без ошибок
- [ ] После wizard показывается экран "Что дальше" с тремя рекомендациями

### 16.2 Ядро и модули

- [ ] 2-контейнерная схема работает. Sandbox-контейнер автоудаляется после теста.
- [ ] Краш одного модуля не останавливает остальные (тест: `kill -9` потока модуля)
- [ ] Watchdog: systemd + Docker автоматически поднимают упавшие контейнеры
- [ ] Integrity Agent обнаруживает изменение файлов ядра за ≤ 30 сек

### 16.3 Голос и LLM

- [ ] STT работает без интернета (тест: `ip link set eth0 down` → команда распознаётся)
- [ ] TTS произносит ответ локально через Piper
- [ ] Режим приватности: GPIO кнопка И голосовая команда отключают микрофон
- [ ] Fast Matcher обрабатывает зарегистрированные команды за < 50ms
- [ ] Биометрия отсутствует в любых исходящих HTTP-запросах (тест через `tcpdump`)

### 16.4 UI и доступ

- [ ] PWA устанавливается на домашний экран телефона. Offline-страница показывает кеш.
- [ ] Tailscale туннель настраивается через ui-core. Pi доступен по MagicDNS.
- [ ] Все 4 режима UI (HEADLESS/KIOSK/FRAMEBUFFER/TTY) работают корректно
- [ ] HTTPS: самоподписанный сертификат, `getUserMedia()` доступен

### 16.5 Безопасность

- [ ] Core API :7070 недоступен снаружи localhost (тест через внешний IP)
- [ ] 5 неверных PIN → блокировка 10 минут, запись в аудит-лог
- [ ] Аудит-лог хранит действия. Доступен только `admin`.
- [ ] Деградация RAM: AUTOMATION останавливается при < 150 MB свободной RAM

### 16.6 SDK и импорт

- [ ] `smarthome new-module` создаёт рабочую структуру
- [ ] `smarthome dev` запускает mock Core API локально
- [ ] Импорт из Home Assistant: устройства и простые автоматизации
- [ ] OAuth QR-flow завершается успешно для Tuya и Home Assistant

---

## 17. Out of Scope — за рамками беты

| Что не входит | Планируется |
|---|---|
| GPG-подписание образа ядра | v0.4 |
| Мультихаб (кластер из нескольких Pi) | v0.5 |
| Встроенный Video Doorbell (видеозвонок) | v0.4 |
| OTA обновления по расписанию без команды платформы | v0.5 |
| UPS / резервное питание | Community модуль |
| Мониторинг Prometheus/Grafana | Community модуль |
| Z-Wave нативно в ядре | v0.4 (только через DRIVER-модуль) |
| Apple HomeKit нативно | v0.5 |
| Мобильное приложение (iOS/Android native) | v1.0 |

---

*SmartHome LK · Core TZ v0.3.0-beta · 2026-03-20 · Open Source / MIT*
