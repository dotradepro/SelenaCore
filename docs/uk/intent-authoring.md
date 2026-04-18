# Як писати голосові інтенти, що справді розпізнаються

Цей гайд кодифікує правила, відкриті під час підняття точності класифікатора інтентів з 57.5% до 96.6% за ~20 ітерацій. Кожне правило нижче — це урок з конкретної регресії. Дотримуйтесь їх, і ваш інтент з першого дня даватиме ≥ 90%.

Застосовується і до **користувацьких модулів** (`modules/*/main.py` з `@intent`), і до **системних модулів** (`system_modules/*/module.py` з `_OWNED_INTENT_META`). Класифікатору байдуже, який це тип — правила однакові.

Про архітектуру класифікатора (MiniLM cosine + Helsinki-перекладач + пост-обробка) читайте [intent-routing.md](../intent-routing.md). Цей документ — про те, як АВТОРИТИ інтенти, що виграють у цій архітектурі.

---

## 60-секундний чек-ліст

Перед тим як відкрити PR із новим інтентом, перевірте:

- [ ] **Ім'я** з префіксом модуля: `<module>.<verb_or_noun>` (наприклад, `weather.current`).
- [ ] **Description** називає основну дію ТА явно виключає сусідні інтенти ("**NOT** for X"). 80–200 символів — не занадто коротко, не роздуто.
- [ ] **Entity types** (якщо інтент націлений на клас пристроїв) — використовують канонічне значення, не вигадувати нові.
- [ ] **Якорі** — 5–10 прикладних фраз у [`INTENT_ANCHORS`](../../system_modules/llm_engine/embedding_classifier.py), що покривають реальні форми мови, плюс Helsinki UK→EN артефакти, якщо підтримується українська.
- [ ] **Handler існує** — кожен запис в `_OWNED_INTENT_META` має відповідний `_handle_*` метод. Мертві інтенти крадуть збіги у реальних.
- [ ] **Кейси в корпусі** додані в [tests/experiments/corpus_generator.py](../../tests/experiments/corpus_generator.py) — ≥ 3 кейси (EN + UK, plain + один варіант).
- [ ] **Bench** — `run_coverage_bench.py` показує: новий інтент ≥ 80%, overall ≥ 97%, distractors 100%.

---

## 1. Ім'я та простір імен

Кожен інтент — `<module>.<verb_or_noun>`, малі літери, через крапку. Приклади:
`weather.current`, `device.on`, `media.play_radio_name`, `clock.set_alarm`.

### Зарезервовані простори (системні модулі)

| Простір | Власник | Для чого |
|---|---|---|
| `device.*` | device-control | Живлення пристроїв, замки, клімат, запити |
| `media.*` | media-player | Відтворення радіо, гучність, треки |
| `house.*` | device-control | Масові операції по дому |
| `clock.*` | clock | Будильники, таймери, нагадування |
| `weather.*` | weather-service | Погода надворі + прогноз |
| `presence.*` | presence-detection | Запити "хто вдома" |
| `automation.*` | automation-engine | Enable/disable/list правил |
| `energy.*` | energy-monitor | Запити споживання енергії |
| `watchdog.*` | device-watchdog | Liveness пристроїв |
| `privacy_on` / `privacy_off` | voice-core | Вимкнути мікрофон |

### Користувацькі модулі

Використовуйте ім'я свого модуля як префікс: `my_weather.umbrella_check`, `garden.water_schedule`. Не розміщуйте нічого під зарезервованим простором — класифікатор переплутає ваш інтент із системним.

---

## 2. Рецепт description

**Це найбільший важіль точності.** Рядок description вбудовується і порівнюється через cosine з фразою користувача — його якість напряму керує маршрутизацією.

### Рецепт

```
<основне дієслово дії> <основний іменник / об'єкт>. <контраст із сусідом>. <конкретні фрази>.
```

1. **Основна дія** — що інтент РОБИТЬ. Одне дієслово, один іменник.
2. **Контрастне речення** — для чого інтент НЕ призначений. Назвати сусіда явно.
3. **Конкретні фрази** — 2–3 дослівні приклади користувача, EN та UK якщо підтримуєте українську.

### Довжина

80–200 символів прози, максимум ~300 разом зі списком фраз. Два типи провалів спостерігалось:

- **Занадто довго** (R2, R8 у журналі тюнінгу): description роздувається понад ~50 слів, центроїд розмивається, піки cosine колапсують. Регресія до −11pp.
- **Занадто коротко** (однорядкове на кшталт `"Set temp"`): класифікатор не має якоря взагалі, не впізнає парафраз. Регресія до −7pp.

### Приклад: `clock.stop_alarm` (злиття двох перекриваних інтентів)

До (два окремі інтенти, 35% точності):
```python
"clock.cancel_alarm": "Cancel / delete an existing alarm by label or position.",
"clock.stop_alarm":   "Silence the alarm that is ringing right now (snooze or dismiss).",
```
Користувачі кажуть "stop" і "cancel" взаємозамінно — класифікатор не міг розрізнити.

Після (злито, 86% точності):
```python
"clock.stop_alarm": (
    "Silence / cancel / dismiss an alarm — covers both "
    "'stop the alarm' when it's ringing AND 'cancel the "
    "morning alarm' when removing from schedule. Single "
    "intent for both verbs (they mean the same thing to "
    "the user)."
),
```

### Приклад: `presence.check_user` vs `presence.who_home` (чіткий контраст)

Обидва запитують стан присутності, але мають різний scope. Descriptions явно контрастують:

```python
"presence.who_home": (
    "List WHO is currently at home — returns names of all "
    "household members present. Open question without a "
    "specific person. Use for 'who's home', 'who is here'. "
    "NOT for 'who are you' (about the assistant)."
),
"presence.check_user": (
    "Check whether ONE SPECIFIC named person is at home. "
    "Query mentions a person's name. Use for 'is Alice home', "
    "'is Bob here'. Contains a proper name — distinguishes "
    "from generic who_home."
),
```

Фраза "contains a proper name — distinguishes from generic who_home" — критичний розділювач. Без неї класифікатор роутив "is Alice home" у `who_home` щоразу.

---

## 3. Канонічні `entity_types`

Колонка `Device.entity_type` в реєстрі використовує фіксований словник. `entity_types` constraint вашого інтента має посилатися на одне з:

| Тип | Типові пристрої |
|---|---|
| `light` | лампи, лампочки, LED-стрічки |
| `switch` | розумні вимикачі |
| `outlet` | розетки, подовжувачі |
| `fan` | вентилятори, стельові fans |
| `air_conditioner` | AC-блоки |
| `thermostat` | розумні термостати |
| `radiator` | обігрівачі, радіатори |
| `humidifier` | зволожувачі |
| `kettle` | електричні чайники |
| `tv` | телевізори |
| `curtain` | штори, жалюзі |
| `vacuum` | робот-пилососи |
| `media_player` | колонки, аудіо-стрімери |
| `door_lock` | розумні замки |
| `speaker` | окремі колонки |
| `sensor` | датчики руху/темп/вологості |
| `camera` | камери безпеки |

### Не винаходьте варіанти

Якщо користувач каже "lamp", "bulb", "light fixture" — всі мапяться на `entity_type="light"`. Додавання `entity_type="lamp"` як нового значення розбиває пул пристроїв і ламає резолв за типом+локацією. Варіантні слова йдуть у [`ENTITY_MAP`](../../system_modules/llm_engine/embedding_classifier.py) (екстрактор сутностей), а не в реєстр.

### Коли використовувати `entity_types` в інтенті

Лише коли інтент семантично стосується вузького набору. Приклад:

```python
"device.set_temperature": dict(
    entity_types=["air_conditioner", "thermostat", "radiator"],
),
```

Без цього "встанови температуру у спальні" матчило б будь-який пристрій у спальні. З цим — резолвер обмежує лише climate-пристроями. Це дало thermostat-кейсам 13% → 91% за одну зміну.

Для generic-інтентів типу `device.on` або `device.off`, що стосуються БУДЬ-ЯКОГО пристрою — залишайте `entity_types=None`.

---

## 4. Якорі — `INTENT_ANCHORS`

Якорі — прикладні речення, попередньо обчислені в centroid інтента. **Тут відбуваються реальні виграші в точності.** Description сам по собі дає грубе співпадіння; якорі загострюють його.

Місце: [`system_modules/llm_engine/embedding_classifier.py`](../../system_modules/llm_engine/embedding_classifier.py), словник `INTENT_ANCHORS` (починається близько рядка 78).

### Скільки

**5–10 якорів на інтент.** Менше → класифікатор не вловлює парафраз. Більше → розмивається (той самий провал, що й роздуте description). Журнал тюнінгу показав +12pp на AC-кейсах лише від додавання 6 якорів.

### Що додавати

1. **Канонічні фрази** — 2–3 найбільш природні способи сказати.
2. **Синоніми та casual-дієслова** — якщо description згадує "flip on" як синонім, додайте "flip on the X" як якір.
3. **Непрямі фрази** — "I want the X on" (device.on), "no need for the X" (device.off), "I want X to work" (device.on).
4. **Helsinki-артефакти перекладу для UK** — див. секцію 5.
5. **Короткі / bare форми** — якщо інтент приймає однослівні команди (`pause`, `resume`, `next`), додайте їх явно як якорі.

### Приклад: `media.resume`

```python
"media.resume": [
    "resume the music",
    "resume",
    "resume playback",
    "continue playing",
    "unpause",
    "keep going",
    # Helsinki-артефакти для UK "продовж" / "продовжи":
    "continued.",
    "continue.",
],
```

Останні два — ключові. Без них `продовж` → Helsinki → `"Continued."` падає нижче порогу й іде в unknown. З ними — впевнено хітить `media.resume`.

---

## 5. Helsinki UK→EN особливості перекладу

Якщо інтент підтримує українське введення, фраза користувача спочатку перекладається моделлю Helsinki opus-mt в англійську ДО того, як потрапляє в embedding-класифікатор. Helsinki lossy і зміщена в бік декларативних речень — не імперативних команд. Це треба враховувати.

### Відомі артефакти (подавати як якорі)

| UK фраза | Вихід Helsinki | Чому це важливо |
|---|---|---|
| `слухай увімкни X` | `"Listen to the X."` | Дієслово `увімкни` випало повністю |
| `запали X` | `"Light the X"` або просто `"X."` | Дієслово `запали` втрачене або спотворене |
| `замок` (lock) | `"Castle."` | Класифіковано як назва місця |
| `зволожувач` | `"Moisturizer."` | Неправильний переклад як косметика |
| `продовж` | `"Continued."` | Декларативно; імператива не лишилось |
| `тихіше` | `"Be quiet."` | Повне речення з одного слова |
| `голосніше` | `"Louder."` | Правильно — спец-обробка не потрібна |
| `постав джаз` | `"Let's jazz."` | Ідіома; працює якщо `media.play_genre` має jazz-якір |

### Зрізання префіксів до перекладу

Деякі префікси зрізаються ПЕРЕД Helsinki — див. [`_strip_uk_listener_prefix()`](../../core/translation/helsinki_translator.py). Поточний список: `слухай`, `послухай`, `дивись`, `скажи будь ласка`, `привіт`. Якщо ваш інтент часто провалюється на UK-фразах із attention-getter'ом, розширюйте цей список, а не додавайте артефактні якорі (чистіше й покриває більший простір фраз).

### Правило великого пальця

Прогоніть ваші UK тест-фрази через перекладач вручну до написання якорів:

```bash
docker exec -t selena-core python3 -c "
from core.translation.local_translator import get_input_translator
t = get_input_translator()
print(t.to_english('ваша фраза тут', 'uk'))
"
```

Що б воно не видало — ТЕ, з чим мають збігатися ваші якорі.

---

## 6. Коли розділяти vs зливати інтенти

### Зливати коли

Одна й та сама дія користувача, різні дієслова. Користувачу байдуже до дієслова — йому важливий результат.

Приклад: `clock.cancel_alarm` + `clock.stop_alarm` були двома інтентами. Користувач каже "cancel the alarm" або "stop the alarm" взаємозамінно — незалежно від того, дзвонить будильник зараз чи запланований на завтра. Один інтент, один handler, що диспатчить за станом: якщо дзвенить — глушимо; інакше — видаляємо з розкладу. Handler робить контекстне рішення, не класифікатор.

### Розділяти коли

Та сама дієслівна фраза, дійсно різні дії з різною семантикою оборотності.

Приклад: `media.pause` vs `media.stop`. Користувачі іноді кажуть "stop the music", коли мають на увазі pause — якорі перекриваються. Але pause оборотний (сесія зберігається), stop — ні (сесія знищена). Тримаючи їх окремими, користувач може відновити "stop, then resume" після паузи. Якщо злити, "stop then resume" стає "stop, then restart from scratch" — видима для користувача регресія.

### Правило великого пальця

Якщо не можете написати чіткий description, що контрастує два інтенти **менш ніж у 20 словах** — це має бути один інтент. `presence.who_home` vs `check_user` проходить тест ("open query" vs "contains a proper name"). `cancel_alarm` vs `stop_alarm` не проходить ("silence one that's ringing" vs "delete a scheduled one" — для користувача однаково).

---

## 7. Post-processing overrides

Розташовані в [`system_modules/llm_engine/intent_router.py`](../../system_modules/llm_engine/intent_router.py), всередині `_try_embedding_classify()` після обчислення cosine-переможця.

### Патерн

```python
if result.intent == <wrong_intent> and <query_condition>:
    result.intent = <correct_intent>
```

### Коли використовувати

**Лише для систематичних misroute'ів, що якорі не виправляють.** Приклад: TV-команди роутилися в `media.play_radio_name`, бо "TV" виглядає як власне ім'я станції. Жодна кількість device.on-якорів не виправляла cosine-колізію. Post-proc правило:

```python
if (
    result.intent.startswith("media.play_")
    and (result.params or {}).get("entity") == "tv"
):
    result.intent = "device.off" if is_off else "device.on"
```

Аналогічно: `house.all_off` override, коли запит містить `all/everything/все`, а класифікатор повернув `device.off`. І `media.volume_up` override для `turn it up` / `louder` ідіом, що misroute'илися в `device.on`.

### Коли НЕ використовувати

Кожен override — крихке правило, що ховає cosine-проблему замість виправлення. Якщо пишете 3+ override для одного інтента — проблема в description та якорях. Спершу виправте їх.

---

## 8. Антипатерн мертвих інтентів

**Якщо оголошуєте інтент у `OWNED_INTENTS` / `_OWNED_INTENT_META`, ви ОБОВ'ЯЗКОВО маєте мати для нього handler.**

Оголошені-але-не-оброблювані інтенти забруднюють candidate-set класифікатора. Cosine вибирає найближче співпадіння з усіх оголошених інтентів — якщо description вашого мертвого інтента лексично близьке до реальної фрази, класифікатор роутить туди, а handler тихо губить команду.

Ми мали це з `media.shuffle_toggle`: оголошений з v0.3, без handler'a у `voice_handler.py`. Користувачі, що говорили "shuffle", отримували команду, але нічого не відбувалось. Видалення оголошення з `_OWNED_INTENT_META` розблокувало downstream-кейси — класифікатор став обирати реальні інтенти.

**Правило:** перш ніж додавати в `_OWNED_INTENT_META`, напишіть handler. Якщо handler не можна написати зараз — не оголошуйте інтент.

---

## 9. Пороги впевненості

Конфігуруються в `config/core.yaml`:

```yaml
intent:
  embedding_score_threshold: 0.25   # cosine переможця має перевищувати
  embedding_margin_threshold: 0.003 # переможець − runner-up має перевищувати
```

Також hardcoded у [`embedding_classifier.py`](../../system_modules/llm_engine/embedding_classifier.py) як `UNKNOWN_THRESHOLD` / `MARGIN_THRESHOLD` — тримайте обидва шари синхронізованими.

**Не тюньте per-intent.** Пороги глобальні, бо розподіл cosine — глобальний. Якщо зміщується весь розподіл (наприклад, після заміни моделі), тюньте один раз, ре-бенчіть, фіксуйте. Поточні значення знайдено проти `paraphrase-multilingual-MiniLM-L12-v2` — інша embedding-модель може потребувати інших порогів.

---

## 10. Тестування та PR-гейт

Кожен новий інтент потребує покриття в корпусі. Без цього не можна виміряти, чи працює він.

### Додавання кейсів

Редагуйте [tests/experiments/corpus_generator.py](../../tests/experiments/corpus_generator.py) — знайдіть список для вашої категорії (або додайте нову) та доповніть:

```python
{"lang": "en", "native": "<фраза користувача>",
 "exp_intent": "<your.intent>",
 "exp_entity": None,  # або entity_type якщо відомий
 "exp_location": None,
 "category": "<category>", "twist": None, "noise": None},
```

Використовувані категорії: `plain`, `variety`, `noise`, `ambiguous`, `all_off`, `all_on`, `media`, `clock`, `weather`, `presence`, `automation`, `system`, `distractor`. Додавайте нову категорію, якщо жодна не підходить — оновіть `_verdict()` в [run_coverage_bench.py](../../tests/experiments/run_coverage_bench.py) відповідно.

### Мінімум на інтент

- 2 кейси EN (plain + один варіант: short, polite, syn або casual)
- 2 кейси UK (plain + один варіант)
- Якщо інтент двомовний, прогоніть Helsinki на UK-кейсах вручну та перевірте англійський вихід — підкоригуйте якорі, якщо вихід незвичний

### Запуск bench

```bash
docker exec -t selena-core python3 /opt/selena-core/tests/experiments/run_coverage_bench.py
```

Займає ~20 секунд. Вивід містить per-category breakdown та повний список провалів.

### PR-гейт (пропоноване CI-правило)

- **Per-intent**: новий інтент має показати ≥ 80% на своїх кейсах (≥ 60% якщо сильно перекривається дієсловами з існуючим — дозволено перекриватися, заборонено красти).
- **Overall**: загальна точність лишається ≥ 97.0% (поточна 96.6%, 0.4pp буфер).
- **Distractors**: мають лишатись 100%. Будь-яке падіння означає over-training якорів з false-positive'ами на chat.

---

## 11. Чек-ліст (повторення)

- [ ] Ім'я: `<module>.<verb_or_noun>` у валідному просторі
- [ ] Description: IS + IS-NOT + 2–3 конкретні фрази, 80–200 символів
- [ ] `entity_types` constraint лише якщо семантично вузьке
- [ ] 5–10 якорів, включно з Helsinki-артефактами якщо UK
- [ ] Handler написаний — не оголошений-без-обробника інтент
- [ ] ≥ 3 кейси в корпусі (EN + UK, plain + variety)
- [ ] Bench: ≥ 80% на новому інтенті, ≥ 97% overall, 100% distractors

---

## 12. Додаток: реальні приклади до/після

### `device.set_temperature` (якорі розблокували)

До: лише description, без якорів. Thermostat 13%, AC 36% на set_temperature-кейсах.

Додані якорі:
```python
"device.set_temperature": [
    "set the air conditioner to 22 degrees",
    "set temperature to 20",
    "set the temperature to 22 degrees in the living room",
    "set the temperature to 22 degrees in the bedroom",
    "set the temperature to 22 degrees in the bathroom",
    "set temperature to 22 in the kitchen",
    "make it 22 degrees in the living room",
    "change the temperature to 22 degrees",
    # Helsinki outputs for UK "встанови температуру":
    "set the air conditioning to 22 degrees.",
    "set twenty-two degrees.",
],
```

Результат: thermostat 91%, AC 94%. Одна правка, +80pp на thermostat.

### `house.all_off` (патерн post-proc override)

Спочатку пробували якорями: "turn off everything", "shut everything down", ~15 якорів. **Регресія −19.6pp**, бо якорі ("turn off …") колізились із якорями `device.off`.

Замінено post-proc override:
```python
if has_all and result.intent in ("device.on", "device.off"):
    new_intent = "house.all_on" if result.intent == "device.on" else "house.all_off"
    result.intent = new_intent
```

Description лишився суворим:
```python
"Whole-house mass off — user said 'all' / 'everything'. "
"NOT for single-device off (use device.off). Triggers "
"ONLY when the query explicitly contains 'all', "
"'everything', 'все', 'всі', 'всё'."
```

Результат: 15/15 кейсів проходять, без регресії на `device.off`.

### `media.volume_up` (bare-verb + обробка ідіом)

Користувачі кажуть просто "louder". Класифікатор спочатку не мав якоря на це — повертав `unknown`. Користувачі кажуть "turn it up", що post-proc misroute'ив у `device.on`. Виправлено:

```python
# Anchors
"media.volume_up": [
    "louder",
    "turn it up",
    "make it louder",
    "increase volume",
    "volume up",
],

# Post-proc override
if result.intent in ("device.on", "device.off"):
    if "turn it up" in q_low or "louder" in q_low:
        result.intent = "media.volume_up"
```

Результат: `media.volume_up` / `media.volume_down` тепер 100%.

---

**Питання / корекції?** Цей документ версіонується в репозиторії. Відкрийте issue або PR, якщо ваш інтент не попадає туди, куди каже гайд — це сигнал, що документ неповний чи неправильний, а не ви.

---

## 13. Уточнюючі запитання до користувача

Іноді класифікатор правильно визначає інтент, але критичний параметр відсутній (`set_temperature` без значення), або в реєстрі є N пристроїв, що всі підходять (`вимкни світло` при 3 лампочках у спальні). Замість того щоб мовчки провалити чи вибрати навмання, **емітіть clarification** — асистент задає уточнююче питання, тримає мікрофон відкритим 10 секунд, потім маршрутизує відповідь проти збереженого контексту.

### Коли fires clarification

Три тригери, з вказівкою хто детектить:

| Тригер | Детектується | Приклад |
|---|---|---|
| `ambiguous_device` | `IntentRouter._disambiguate_device` | 2+ пристроя одного типу, без кімнати |
| `missing_param` | **Хендлер вашого модуля** | `set_temperature` без числа |
| `low_margin` | `IntentRouter._try_embedding_classify` | Margin embedding у діапазоні (0.003, 0.015) |

`ambiguous_device` і `low_margin` спрацьовують автоматично в роутері — нічого додатково не робіть. `missing_param` — ВАШЕ завдання.

### Емісія `missing_param` з модуля

Використовуйте `SystemModule.request_clarification()`:

```python
from core.module_loader.system_module import SystemModule

class ClimateModule(SystemModule):
    async def _on_voice_intent(self, event):
        params = event.payload.get("params", {})
        intent = event.payload.get("intent")

        if intent == "climate.set_temperature":
            if not params.get("value"):
                await self.request_clarification(
                    pending_intent=intent,
                    pending_params=dict(params),
                    question_key="clarify.missing_value",  # ключ canned-prompt'а
                    reason="missing_param",
                    hint="temperature",                    # вставляється у питання
                    param_name="value",                    # slot куди піде відповідь
                )
                return  # ВАЖЛИВО: return одразу — VoiceCore
                        # переходить у AWAITING_CLARIFICATION
```

Параметри:

- `pending_intent`: інтент, що re-fire після відповіді користувача.
- `pending_params`: зібрані параметри (мержаться з відповіддю на turn 2).
- `question_key`: ключ у [action_phrasing.py](../../system_modules/voice_core/action_phrasing.py). Canned catalog: `clarify.missing_value` / `clarify.which_room` / `clarify.which_device` / `clarify.low_confidence` / `clarify.cancelled` / `clarify.timed_out`. Реєструйте свій модуль-специфічний якщо треба.
- `hint`: вільний текст, підставляється в питання ("what `temperature`?" / "what `fan speed`?").
- `param_name`: slot у `pending_params`, який заповнює відповідь. Default `"value"`.
- `allowed_values`: для enum-слотів (mode / fan-speed). Fuzzy-match проти списку. Числовий парсинг йде першим.
- `timeout_sec`: default 10.0. Зазвичай не перевизначайте.

**Обов'язково `return` одразу** після виклику. VoiceCore audio loop переходить у `AWAITING_CLARIFICATION`; не блокуйте після виклику.

### Як матчиться відповідь

[`IntentRouter.route_clarification()`](../../system_modules/llm_engine/intent_router.py) запускає fast-path:

1. **Позиційна референція** — `"first"` / `"the second"` / `"перший"` / `"друге"` → кандидат за індексом. EN + UK тільки (§R9).
2. **Match за кімнатою** — двомовно з morphology-tolerance (UK відмінки через SequenceMatcher similarity ≥ 0.70).
3. **Match за ім'ям пристрою** — fuzzy similarity ≥ 0.75 проти `name` кандидатів.
4. **Числовий парсинг** — цифри (`"22"`) або слова (`"twenty-two"`).
5. **Fuzzy-match allowed-values** — для enum-слотів.
6. **Ствердження** — `"yes"` / `"ok"` / `"так"` → виграшник.

При успіху: оригінальний інтент re-fire з merged params.

При провалі: canned `clarify.cancelled` + повернення в idle.

### Тестування

Додайте fixture до [clarification_fixtures.py](../../tests/experiments/clarification_fixtures.py):

```python
{
    "name": "my_module.missing_param.numeric",
    "lang": "en",
    "turn_2_text": "5",
    "synthetic_pending": {
        "reason": "missing_param",
        "question_key": "clarify.missing_value",
        "hint": "duration",
        "param_name": "duration_sec",
        "pending_intent": "my_module.start",
        "pending_params": {},
        "timeout_sec": 10.0,
    },
    "expected_final_intent": "my_module.start",
    "expected_final_params": {"duration_sec": "5"},
},
```

Прогін:

```bash
docker exec -t selena-core python3 /opt/selena-core/tests/experiments/run_clarification_bench.py
```

Ціль: ваша fixture pass. Додайте companion fuzzy-fail (відповідь що не матчить, `allow_cancelled: True`).

### Не треба

- **Не треба** реалізовувати свій "ask and wait" loop. Використовуйте `request_clarification`. State machine живе в VoiceCore і має залишатися там — кілька модулів за мікрофон одночасно зламають сесію.
- **Не ланцюжки clarification**. MVP — один раунд на команду. "Яка кімната? спальня. Яка лампа в спальні?" поза скоупом, заплутає користувача.
- **Не ставте `timeout_sec`** нижче 5 або вище 15. Менше 5 — надто агресивно; більше 15 — дратівна тиша.
