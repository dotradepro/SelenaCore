# Система провайдерів

`device-control` — це runtime-pluggable система провайдерів. Кожен провайдер — Python-пакет, що реалізує інтерфейс `DeviceDriver` і може бути встановлений без перезбірки контейнера чи перезапуску ядра.

Цей документ — користувацький вступ. Для технічних деталей — ORM `DriverProvider`, hot-reload контракт, стійкість до перезапуску, сумісність з Integrity Agent — див. [provider-system-and-modules.md](provider-system-and-modules.md).

## Яку проблему вирішує

Протоколи розумного дому постійно змінюються. Раніше додавання нового виробника означало перезбірку Docker-образу та новий реліз. З системою провайдерів:

- Нові сімейства пристроїв поставляються як **опціональні пакети**, що встановлюються в один клік.
- Хеш-маніфест Integrity Agent не порушується — провайдери знаходяться за межами `/opt/selena-core/core/**/*.py`.
- Невдалі встановлення ізольовані та відображаються через `last_error` у картці провайдера.
- Видалення провайдера — один клік; Device Registry зберігає пристрої, але повідомляє їх як offline до повторного підключення.

## Вбудовані провайдери

Попередньо встановлені та завжди доступні.

| Провайдер    | Протокол            | Пакет           | Типи пристроїв              | Хмарний акаунт |
|--------------|---------------------|-----------------|-----------------------------|----------------|
| `tuya_local` | Tuya LAN API        | `tinytuya`      | light, switch, outlet, A/C  | ❌             |
| `tuya_cloud` | Tuya Sharing SDK    | `tuya-sharing`  | Усі категорії Tuya          | ❌             |
| `gree`       | Gree UDP / AES-ECB  | `greeclimate`   | air_conditioner             | ❌             |
| `mqtt`       | MQTT bridge (relay) | (використовує `protocol_bridge`) | будь-який    | ❌             |

`tuya_cloud` не потребує облікового запису розробника Tuya — використовує той самий Device Sharing SDK, що й мобільний додаток Smart Life.

`gree` охоплює всю родину протоколу Gree: Pular, Cooper&Hunter, EWT, Ewpe Smart та більшість ребрендованих моделей.

## Опціональні провайдери

Не попередньо встановлені. Встановіть з UI, коли потрібно.

| Провайдер      | Протокол             | Пакет                          | Типи пристроїв          | Примітки                                   |
|----------------|----------------------|--------------------------------|-------------------------|--------------------------------------------|
| `philips_hue`  | Hue Bridge LAN API   | `phue`                         | light                   | Poll-based (3 с), потрібне натискання кнопки на bridge при першому з'єднанні |
| `esphome`      | ESPHome native API   | `aioesphomeapi`                | switch, light, sensor, outlet | Push-based, автовиявлення сутностей при з'єднанні |
| `zigbee2mqtt`  | Zigbee2MQTT MQTT bridge | *(немає — використовує protocol_bridge)* | light, switch, sensor | Потрібен запущений Z2M + MQTT-брокер       |
| `matter`       | Matter / Thread      | `python-matter-server[client]` | light, switch, outlet, sensor, lock, thermostat | Потрібен контейнер-супутник matter-server  |

Нові провайдери можна додавати без змін у ядрі.

## Встановлення провайдера

### Через UI (рекомендовано)

1. Відкрийте **Settings → device-control → Providers**.
2. Знайдіть провайдер у каталозі.
3. Натисніть **Install**. Pip працює у фоновому потоці; прогрес відображається на картці.
4. Коли статус зміниться на `loaded`, натисніть **Scan** (або імпортуйте пристрої).

### Через API

```http
POST /api/ui/modules/device-control/providers/{provider_id}/install
Authorization: Bearer <module_token>
```

Статус видно за адресою:

```http
GET /api/ui/modules/device-control/providers
```

Ті самі ендпоінти надають `uninstall` та `reload`.

## Імпорт пристроїв

Кожен провайдер має свій процес імпорту. Найпоширеніші:

- **Tuya local:** сканування LAN — пристрої, що відповідають на Tuya discovery beacon, відображаються для імпорту.
- **Tuya cloud:** вхід через Tuya Sharing SDK QR flow.
- **Gree / Pular:** UDP broadcast сканування в локальній підмережі.
- **Philips Hue:** введіть IP bridge, натисніть фізичну кнопку link на bridge, потім додайте лампи за ID.
- **ESPHome:** введіть IP пристрою (порт 6053 за замовчуванням); драйвер автоматично виявляє всі сутності при з'єднанні.
- **Zigbee2MQTT:** додайте пристрої з їх Z2M `friendly_name`; стан передається через `protocol_bridge`.
- **Matter:** введіть setup code з пристрою; контейнер matter-server виконує комісіонування та керує ним.

Коли пристрій імпортовано, `device-control` публікує `device.registered` зі збагаченим payload (`entity_type`, `location`, `capabilities`). Три модулі слухають та реагують автоматично:

| Підписник         | Реакція                                                 |
|-------------------|---------------------------------------------------------|
| `energy_monitor`  | Автоматично створює джерело енергії                     |
| `climate`         | Інвалідує кеш кімнат при `entity_type=air_conditioner|thermostat` |
| `lights_switches` | Інвалідує кеш кімнат при `entity_type=light|switch|outlet` |

Не потрібно нічого підключати вручну.

## Створення власного провайдера

Провайдер — це Python-пакет, що експортує клас, який реалізує `DeviceDriver` (`system_modules/device_control/drivers/base.py`):

```python
class DeviceDriver(ABC):
    protocol: str = ""

    def __init__(self, device_id: str, meta: dict[str, Any]) -> None: ...

    async def connect(self) -> dict[str, Any]:        # відкрити з'єднання, повернути початковий стан
    async def disconnect(self) -> None:                 # закрити ресурси (ідемпотентно)
    async def set_state(self, state: dict) -> None:     # застосувати часткове оновлення стану
    async def get_state(self) -> dict[str, Any]:        # повернути поточний знімок стану
    def stream_events(self) -> AsyncGenerator[dict]:    # push loop (видає зміни стану)
    def consume_metering(self) -> dict | None:          # опціональне зчитування потужності
```

Три патерни драйверів існують у кодовій базі:

| Патерн | Приклад | Коли використовувати |
|--------|---------|----------------------|
| **EventBus делегування** | `mqtt_bridge`, `zigbee2mqtt` | Протокол обробляється іншим модулем (напр. `protocol_bridge`) |
| **Poll + diff** | `gree`, `philips_hue` | Пристрій не надсилає push; опитування кожні N секунд, yield при зміні |
| **Push + queue** | `matter`, `esphome` | Бібліотека надає push callback; маршрутизація в `asyncio.Queue` |

Зареєструйте в `system_modules/device_control/providers/catalog.py` (для вбудованих) або поставте як pip-пакет з посиланням з каталогу.

Повний контракт розробника — ORM `DriverProvider`, loader, протокол hot-reload, межа integrity-agent — документовано в [provider-system-and-modules.md](provider-system-and-modules.md).

## Усунення несправностей

- **`last_error` відображається на картці** — відкрийте картку провайдера; повідомлення про помилку дослівно з pip або loader.
- **Провайдер застряг у `installing`** — перевірте `docker compose logs selena-core --tail=200`. Вивід pip логується з логером `device_control.providers`.
- **Пристрої offline після видалення** — очікувана поведінка. Перевстановіть провайдер або передайте пристрої іншому.
- **Integrity Agent позначив провайдер** — провайдери мають бути встановлені під `/var/lib/selena/providers/` (за межами glob хешування core). Якщо бачите це, створіть issue.

## Див. також

- [provider-system-and-modules.md](provider-system-and-modules.md) — внутрішня архітектура
- [climate-and-gree.md](../climate-and-gree.md) — деталі протоколу Gree
- [../modules.md#device_control](../modules.md#device_control) — довідник модулів
