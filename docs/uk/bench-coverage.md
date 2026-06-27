# Coverage Bench класифікатора інтентів

Coverage bench — це regression-гейт для голосового класифікатора інтентів SelenaCore. Генерує тест-кейси з **живого реєстру пристроїв** і проганяє кожен через повний production-пайплайн (Helsinki-переклад → embedding-класифікатор → каскад пост-обробки → резолв інтента). Результат — розбивка точності по категоріях, що ловить регресії класифікатора до production.

Поточна production-baseline: **96.6%** на 1114 кейсах, p50 15 ms, p95 17 ms, нульовий false-positive на distractor'ах.

Про архітектуру класифікатора — [intent-routing.md](intent-routing.md). Про правила авторингу інтентів — [intent-authoring.md](intent-authoring.md).

---

## Що тестує

Корпус збирається з двох джерел:

### Згенеровані з реєстру кейси (~1000)

Для кожної комбінації `(entity_type, location)` у [реєстрі пристроїв](api-reference.md#device-registry) [corpus_generator.py](../../tests/experiments/corpus_generator.py) генерує кейси в категоріях:

- **plain** — канонічна фраза (`"turn on the light in the bedroom"`)
- **variety** — 5 парафразних варіантів: `syn`, `polite`, `short`, `indirect`, `casual`
- **noise** — 5 реальних STT-деградацій: `filler`, `typo`, `stutter`, `context`, `long`
- **ambiguous** — той самий інтент без кімнати, щоб перевірити шлях `needs_location`

Все генерується EN та UK (корпус двомовний).

### Куровані категорії (~60)

Рукописні кейси для інтентів, які не можна авто-згенерувати з реєстру:

| Категорія | Розмір | Покриті інтенти |
|---|---|---|
| `media` | 26 | `media.play_*`, `pause`/`resume`/`stop`, `next`/`previous`, `volume_*`, `whats_playing` |
| `all_off` / `all_on` | 15 | `house.all_off` / `house.all_on` з опційними entity + location фільтрами |
| `clock` | 14 | `clock.set_alarm`, `set_timer`, `set_reminder`, `list_alarms`, `stop_alarm`, `cancel_timer` |
| `weather` | 6 | `weather.current`, `forecast`, `temperature` |
| `presence` | 6 | `presence.who_home`, `check_user`, `status` |
| `automation` | 7 | `automation.list`, `enable`, `disable` |
| `system` | 19 | `watchdog.*`, `energy.*`, `privacy_*`, `device.query_temperature`, `set_fan_speed`, `media.play_search` |
| `distractor` | 9 | chat / безглуздя — **не має** давати device-інтент |

Кожен інтент, що належить кожному модулю, представлений хоча б один раз.

---

## Запуск бенча

### Передумови

- Core-контейнер піднятий і healthy (`sudo docker ps | grep selena-core`)
- Helsinki-перекладач активний (`translation.engine = helsinki` в `config/core.yaml`)
- Embedding-модель присутня за `intent.embedding_model_dir` (default: `/var/lib/selena/models/embedding/paraphrase-multilingual-MiniLM-L12-v2/`)
- Реєстр заповнений типовими пристроями — запустіть [scripts/seed_missing_types.py](../../scripts/seed_missing_types.py), якщо певні типи відсутні

### Виконання

```bash
sudo docker exec -t selena-core python3 /opt/selena-core/tests/experiments/run_coverage_bench.py
```

Займає ~20 секунд на Jetson Orin / Pi 5. Вивід:

```
Accuracy: 1076/1114 (96.6%)

By category:
  plain         94/94  ████████████████████ 100.0%
  variety      358/383 ██████████████████    93.5%
  noise        437/441 ███████████████████   99.1%
  ...
```

JSON-підсумок потрапляє до `_private/coverage_bench_results.json` (container-side: `/opt/selena-core/_private/coverage_bench_results.json`). Копіювання на хост:

```bash
sudo docker cp selena-core:/opt/selena-core/_private/coverage_bench_results.json \
               _private/coverage_bench_results.json
sudo chown $USER _private/coverage_bench_results.json
```

### Цикл ітерацій під час тюнінгу

Кожен раунд тюнінгу (description / anchor / threshold) дешевий: ~20 с bench + ~30 с core-restart. Типовий цикл:

```bash
# 1. edit embedding_classifier.py anchors OR intent description
# 2. restart core
sudo docker restart selena-core
until sudo docker ps --format '{{.Names}}: {{.Status}}' | grep -q 'selena-core.*healthy'; do
    sleep 3
done

# 3. run bench, tee log, copy JSON, diff against previous
TS=$(date +%H%M); LOG=_private/bench_runs/round_${TS}.log
sudo docker exec -t selena-core python3 /opt/selena-core/tests/experiments/run_coverage_bench.py 2>&1 | tee "$LOG"
sudo docker cp selena-core:/opt/selena-core/_private/coverage_bench_results.json _private/coverage_bench_results.json
sudo chown $USER _private/coverage_bench_results.json
python3 _private/compare_rounds.py     # prev vs current diff
```

---

## Візуалізація результатів

[scripts/render_bench_svg.py](../../scripts/render_bench_svg.py) генерує scalable SVG summary із найсвіжішого JSON.

```bash
python3 scripts/render_bench_svg.py
# → _private/bench_viz/intent-bench.svg
```

Вивід живе в `_private/` (gitignored), бо bench-результати машинно-локальні та змінюються разом з реєстром. Про публікацію — нижче.

### Конвертація в PNG

Без matplotlib / rsvg-convert залежностей. Використовуйте headless Chrome:

```bash
cat > /tmp/wrap.html <<'HTML'
<!DOCTYPE html>
<html><head><style>*{margin:0;padding:0}html,body{background:#0d1117}</style></head>
<body><img src="/home/YOU/SelenaCore/_private/bench_viz/intent-bench.svg"/></body></html>
HTML

google-chrome --headless --no-sandbox --disable-gpu --hide-scrollbars \
    --device-scale-factor=2 --window-size=960,560 \
    --screenshot=/home/YOU/SelenaCore/_private/bench_viz/intent-bench.png \
    file:///tmp/wrap.html
rm /tmp/wrap.html
```

### Публікація результату

Коли потрібно опублікувати поточний score (release notes, README badge, сайт):

1. Перегенеруйте SVG зі свіжого bench
2. Завантажте вручну з `_private/bench_viz/` на ціль публікації — **не комітьте бінарі в репо**, бо вони стають застарілими, щойно реєстр або класифікатор змінюються
3. Для документів, що посилаються на конкретне число, зазначайте "станом на v0.3.X" з commit SHA — не hardcode'те значення

---

## Інтерпретація виводу

### Категорії

| Категорія | Що означає успіх |
|---|---|
| **plain** | Канонічні фрази класифікуються правильно. Будь-який провал тут — критичний баг description/якорів — має бути 100%. |
| **variety** | Парафрази (syn / polite / short / indirect / casual) класифікуються правильно. Очікуване ~93–97%. |
| **noise** | Реальний STT-шум (заповнювачі, typos, stutter, контекст, довгі preambles) не ламає класифікатор. Очікуване ~99%. |
| **ambiguous** | Користувач не сказав кімнату, а ≥ 2 пристроя матчать — router додає `ambiguous=True`, модуль питає "у якій кімнаті?". Має бути 100%. |
| **all_off** / **all_on** | Whole-house команди йдуть в `house.all_*`, не в окремий `device.off`. Має бути 100%. |
| **media** | Bare-verb playback команди (`pause`, `next`, `louder`) + named-station. ~85–95% — UK bare-verb форми страждають від Helsinki-quirks. |
| **clock** / **weather** / **presence** / **automation** / **system** | Cross-module coverage. Clock / automation найскладніші, бо їхні інтенти ділять дієслова; очікуване 85–95%. Weather зазвичай 100%. |
| **distractor** | Chat / безглуздя — **не має** давати device-інтент. **100% — жорстка планка** — будь-що нижче означає, що класифікатор вивчив false-positive якорі. |

### Рухомий поріг точності

- **Overall ≥ 97.0%** — поточна конфігурація; будь-яке падіння блокує PR merge (CI-проводка — TODO).
- **Per-new-intent ≥ 80%** на своїх кейсах (≥ 60% якщо ділить дієслова з існуючим інтентом).
- **Distractors = 100%** — без винятків.

---

## Коли bench падає

### Потік діагностики

1. Прочитайте розбивку по категоріях. Якщо одна категорія впала — це scoped-регресія. Поверніться до останньої зміни коду, що впливала на цей namespace.
2. Прочитайте повний список провалів (до 200 збережено в JSON). Паттерни:
   - **Той самий інтент виграє скрізь** → description або якорі цього інтента занадто широкі, крадуть матчі.
   - **`unknown` скрізь в одній категорії** → пороги занадто суворі АБО Helsinki калічить UK-вхід (проженіть фразу через `get_input_translator().to_english()` вручну).
   - **Entity extraction повертає `None`** → keyword відсутній у `ENTITY_MAP` в [embedding_classifier.py](../../system_modules/llm_engine/embedding_classifier.py).
3. Для UK-провалів — порівняйте вихід Helsinki з вашими якорями. Невідповідність там — найчастіша root cause.

### Типові фікси (за ймовірністю)

1. Додати відсутні anchor-речення (включно з Helsinki-артефактами для UK)
2. Загострити IS / IS-NOT контраст description'a
3. Додати post-processing override для систематичних misroute'ів (помірно — див. [intent-authoring.md](intent-authoring.md#7-post-processing-overrides))
4. Підкрутити поріг (лише якщо розподіл cosine зсунувся — наприклад, після заміни моделі)
5. Злити два інтенти, що ділять дієслова (див. [intent-authoring.md](intent-authoring.md#6-коли-розділяти-vs-зливати-інтенти))

Кожен фікс — одна зміна, потім re-run. Якщо точність не виросла на ≥ 0.5 pp — revert і пробуйте іншу гіпотезу. Саме ця дисципліна дала підйом 57.5 → 96.6% за 20 раундів.

---

## Clarification bench (2-turn flow)

Окремий runner живе в [tests/experiments/run_clarification_bench.py](../../tests/experiments/run_clarification_bench.py). Тестує multi-turn clarification-шлях окремо від основного корпусу:

```
turn 1   router.route(utterance_1)
         → IntentResult.clarification set (ambiguous / missing_param / low_margin)
turn 2   router.route_clarification(utterance_2, pending)
         → merged intent re-fire OR canned cancel
```

Два runner'и роз'єднані навмисно (plan §R7): основний bench stateless single-turn, додавання two-turn логіки забруднить `_verdict()`. Fixture'и в [clarification_fixtures.py](../../tests/experiments/clarification_fixtures.py) — 13 курованих сценаріїв:

- Resolve by room (EN + UK, з morphology tolerance для UK відмінків)
- Resolve by positional reference (`"the first"` / `"перший"`)
- Resolve by device name (fuzzy)
- Resolve by numeric / word-form value (`"22"` / `"twenty-two"`)
- Allowed-value matching для set_mode / set_fan_speed
- Cross-language reply (EN question, UK answer)
- Fuzzy-fail → cancel

### Запуск

```bash
sudo docker exec -t selena-core python3 \
    /opt/selena-core/tests/experiments/run_clarification_bench.py
```

~5 секунд. JSON → `_private/clarification_bench_results.json`.

### Пороги

- **≥ 80% overall** на fixture-списку (поточне 92.3%).
- Кожна нова clarification-фіча має додавати ≥ 2 fixture що її exercise'ять.

### Synthetic-pending fixtures

`missing_param` емітиться хендлером модуля (device-control `_intent_to_state` ValueError trap), не роутером. Fixture'и з `synthetic_pending` dict обходять turn 1 — dict передається в `route_clarification()` напряму. Зберігає покриття matcher-логіки без підняття аудіо-loop.

### Що НЕ покривається

- Wake-word під час `AWAITING_CLARIFICATION` cancel'ить pending — це audio-loop state-machine, потребує mic-input симуляції. Тільки integration testing.
- Real-time silence → `clarify.timed_out` — timing аудіо-loop'а, не-benchable.

Обидва покриваються **manual acceptance сценаріями** з plan'у; запускайте на живому залізі перед release'ом що зачіпає цю підсистему.
