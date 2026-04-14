# Нативне розгортання — гайд для різних дистрибутивів

Цей документ описує запуск SelenaCore на Linux-дистрибутивах поза основною
тестованою родиною Debian/Ubuntu (Raspberry Pi OS, Jetson L4T, Debian 12,
Ubuntu 22.04/24.04). Інсталятор тепер автоматично визначає пакетний менеджер
і надає best-effort підтримку для Fedora/RHEL, Arch та openSUSE.

> **Протестовано на:** Jetson L4T, Raspberry Pi OS, Debian 12, Ubuntu 22.04/24.04.
>
> **Community-підтримка (best-effort):** Fedora 40+, RHEL 9 / Rocky / Alma,
> Arch Linux / Manjaro, openSUSE Tumbleweed / Leap 15.5+.
>
> Повідомити про проблеми: <https://github.com/dotradepro/SelenaCore/issues>

## Вимоги (для всіх дистрибутивів)

- systemd (SelenaCore використовує systemd-юніти `smarthome-core`,
  `smarthome-agent`, `piper-tts`, `selena-display`). Non-systemd
  init-системи (OpenRC на Alpine, runit на Void) не підтримуються — wizard
  пропустить встановлення нативних сервісів і надрукує інструкції.
- 64-бітний CPU (amd64 / arm64). armv7 Raspberry Pi 3 **не підтримується**.
- Мінімум 4 GB RAM (рекомендовано 8 GB для локальної LLM).
- Docker Engine 24+ з плагіном Compose.
- Python 3.9+ на хості (інсталятор встановить новіший через `uv`, якщо
  системний застарий).
- Для власників NVIDIA GPU: драйвери NVIDIA + Container Toolkit (інсталятор
  обробляє на apt/dnf/pacman/zypper).

## Установка однією командою

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
sudo ./install.sh
```

Інсталятор:

1. Визначає пакетний менеджер (`apt` / `dnf` / `pacman` / `zypper`).
2. Встановлює базові пакети (curl, git, Python, ffmpeg, arp-scan,
   NetworkManager, build toolchain, …).
3. Встановлює Docker Engine + Compose-плагін.
4. Встановлює нативні AI-рантайми (Ollama + Piper TTS) на хості.
5. Створює системного користувача `selena` та директорії.
6. Запускає docker-стек (`core`, `agent`) на порту 80.
7. Друкує `http://<lan-ip>/` — відкрийте в браузері для проходження wizard.

Далі wizard завантажує STT/TTS/LLM моделі, створює адмін-користувача,
реєструє пристрій на платформі та вмикає systemd-сервіси.

## Користувач сесії та прив'язка kiosk

SelenaCore запускає два класи сервісів:

| Сервіс | Від імені | Чому |
|---|---|---|
| `smarthome-core.service` (Docker) | системний `selena` | Фоновий демон — не потребує seat, не потребує home |
| `selena-display.service` (cage kiosk) | **оператор** | Wayland-композитор потребує login seat; у `root` його нема |
| `piper-tts.service` (опційно) | **оператор** | Моделі голосів лежать у `~/.local/share/piper/` |

"Оператор" — людина, що сидить перед пристроєм (або, для headless —
будь-який не-root користувач). За замовчуванням `install.sh` бере
`$SUDO_USER` — того, хто запустив `sudo ./install.sh`.

### Як обрати правильного користувача

```bash
# Випадок 1 — ви залоговані як оператор, робите sudo:
pi@raspberrypi:~ $ sudo ./install.sh
# → kiosk/piper прив'язуються до `pi`, `pi` додається до docker+selena

# Випадок 2 — SSH під root (без sudo), або $SUDO_USER порожній/root,
# ОБОВ'ЯЗКОВО передайте --kiosk-user:
root@box:~ # ./install.sh --kiosk-user=alice
# → kiosk/piper прив'язуються до `alice`

# Випадок 3 — install.sh відмовиться прив'язувати kiosk до root.
# Якщо --kiosk-user не передано і $SUDO_USER порожній/root,
# установка selena-display.service ПРОПУСКАЄТЬСЯ з попередженням.
# Все решта ставиться нормально — kiosk можна увімкнути пізніше
# повторним запуском install.sh з --kiosk-user=NAME.
```

### Зміна оператора потім

Перезапустіть `sudo ./install.sh` з сесії нового користувача, або
передайте `--kiosk-user=<newname>`. Юніти `selena-display.service` і
`piper-tts.service` перегенеруються під нового користувача; старий
лишається у групах `docker` і `selena` (нешкідливо). Деінсталяція не
потрібна.

### Що з системним користувачем `selena`?

Створюється автоматично інсталятором — системний користувач (без shell,
без логіну), якому належить docker-контейнер, `/var/lib/selena` і
`/secure`. Ви ніколи не логінитесь як `selena`. Людей-операторів
**додають до цієї групи** щоб мали доступ до спільних log- і
model-директорій.

## Нотатки по дистрибутивах

### Debian / Ubuntu / Raspberry Pi OS / Jetson L4T

Без додаткових кроків. Це основний протестований шлях.

### Fedora / RHEL / Rocky / Alma (dnf)

```bash
sudo dnf install -y git curl
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
sudo ./install.sh
```

Нотатки:
- Docker встановлюється через `https://get.docker.com`, який налаштовує
  репозиторій `docker-ce` і ставить Compose-плагін.
- NVIDIA Container Toolkit встановлюється з
  `nvidia.github.io/libnvidia-container/stable/rpm/`.
- SELinux: якщо увімкнено в enforcing-режимі — можливо треба перемаркувати
  `/var/lib/selena` і `/secure`:
  ```bash
  sudo chcon -R -t container_file_t /var/lib/selena /secure
  ```
- Файрвол: `firewalld` за замовчуванням увімкнено. Відкрийте порт 80:
  ```bash
  sudo firewall-cmd --permanent --add-service=http && sudo firewall-cmd --reload
  ```

### Arch Linux / Manjaro (pacman)

```bash
sudo pacman -Syu --needed git curl
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
sudo ./install.sh
```

Нотатки:
- Docker та NVIDIA Container Toolkit беруться з офіційних Arch-репо
  (`community`). Якщо `nvidia-container-toolkit` відсутній — встановіть з AUR.
- `cog` і `seatd` не встановлюються автоматично на Arch — лише `cage` +
  `wtype`. Kiosk-режим працює; `selena-display.service` використає `cage`
  напряму.

### openSUSE Tumbleweed / Leap (zypper)

```bash
sudo zypper install -y git curl
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
sudo ./install.sh
```

Нотатки:
- Docker встановлюється через `https://get.docker.com`.
- NVIDIA toolkit repo додається через `zypper addrepo` з RPM-дзеркала NVIDIA.
- AppArmor/SELinux зазвичай вимкнено; додаткові кроки не потрібні.

## Ручна установка (без `install.sh`)

Якщо бажаєте зробити все власноруч:

```bash
# 1. Встановити базові пакети (адаптуйте під ваш дистрибутив)
#    Повний список дивіться у install.sh → install_host_packages.

# 2. Встановити Docker + Compose
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

# 3. Встановити Ollama (хост-сервіс, усі дистрибутиви)
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama

# 4. Встановити Piper TTS Python-пакет (від не-root користувача)
pip install --user piper-tts aiohttp

# 5. Створити директорії
sudo install -d -m 0755 /var/lib/selena/models/{piper,vosk,whisper} \
    /var/lib/selena/speaker_embeddings /var/log/selena
sudo install -d -m 0750 /secure

# 6. Клонування та конфіг
git clone https://github.com/dotradepro/SelenaCore.git /opt/selena-core
cd /opt/selena-core
cp config/core.yaml.example config/core.yaml
cp .env.example .env

# 7. Білд фронтенду + старт стека
npx vite build
docker compose up -d --build

# 8. Staging systemd-юнітів (wizard їх увімкне)
sudo bash scripts/install-systemd.sh

# 9. Відкрийте http://<ip>/ та пройдіть wizard
```

## Вирішення проблем

### Інсталятор виходить з "PKG not found"

Ви на дистрибутиві без жодного з apt/dnf/pacman/zypper. SelenaCore "з коробки"
не підтримує nixpkgs, xbps (Void), apk (Alpine), eopkg (Solus). Використайте
розділ **Ручна установка** вище, адаптуючи імена пакетів вручну.

### Ollama не стартує

```bash
systemctl status ollama
journalctl -u ollama -n 50
```

Найчастіша причина: відсутні GPU-драйвери або порт 11434 зайнятий. Змініть
через `OLLAMA_HOST=127.0.0.1:11435` у
`/etc/systemd/system/ollama.service.d/override.conf` і оновіть `llm.ollama_url`
у `config/core.yaml`.

### Piper TTS сервіс падає

`scripts/piper-tts.service` містить шаблонні плейсхолдери `__USER__`,
`__HOME__`, `__PYTHON__`. Якщо Piper падає, перезапустіть:
```bash
sudo bash scripts/install-systemd.sh
```
який повторно підставить плейсхолдери з поточного конфіга.

### Контейнер не бачить PulseAudio

docker-compose.yml монтує `/run/user/${HOST_UID}/pulse`. Якщо сесія
rootless / headless / linger вимкнено, сокет може бути відсутній. Увімкніть
linger:
```bash
sudo loginctl enable-linger "$USER"
```
Потім перестворіть compose-стек:
```bash
docker compose down && docker compose up -d
```

### arp-scan повертає порожній список

`arp-scan` потребує raw-socket capability. Compose запускає контейнер із
`network_mode: host` + `privileged: true` — це обов'язково. На non-Debian
перевірте, що контейнер успадкував ці прапорці:
```bash
docker inspect selena-core | grep -E 'NetworkMode|Privileged'
```

### SELinux блокує контейнер (Fedora/RHEL)

```bash
sudo setenforce 0   # перевірити
# Якщо виправило — перемаркувати шляхи установки:
sudo chcon -R -t container_file_t /var/lib/selena /secure /opt/selena-core
sudo setenforce 1
```

## Повідомлення про тестування non-Debian

Якщо ви запустили SelenaCore на дистрибутиві поза тестованим списком — ми
будемо вдячні за звіт. Створіть issue з:

- Дистрибутив + версія (`cat /etc/os-release`)
- Вивід `sudo ./install.sh --dry-run`
- Перша помилка або попередження
- Вивід `docker compose logs core --tail 50`

Деталі див. у [CONTRIBUTING.md](CONTRIBUTING.md).
