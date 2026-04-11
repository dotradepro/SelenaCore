# Бенчмарк класифікації інтентів

Перевіряє єдиний промт `system` на різних моделях Ollama та виводить точність,
латентність і валідність JSON-виводу.

## Що вимірюється

| Метрика | Значення |
|---|---|
| `intent_acc` | % випадків, коли модель повернула очікуваний інтент |
| `params_acc` | % випадків, коли params (entity, location, value, ...) збіглися |
| `json_valid` | % сирих відповідей, що парсяться як валідний JSON |
| `p50 ms` / `p95 ms` | Латентність round-trip через `core.llm.llm_call()` |

## Запуск

```bash
docker compose exec core python tests/benchmark/run_intent_bench.py \
    --corpus tests/benchmark/intent_corpus.jsonl \
    --models qwen2.5:0.5b,qwen2.5:1.5b,qwen2.5:3b,phi3-mini:3.8b,gemma3:1b \
    --runs 1 \
    --out /tmp/intent_bench.json
```

Моделі мають бути вже завантажені в Ollama (`ollama pull qwen2.5:1.5b` на хості).
Бенчмарк використовує той самий `core.llm.llm_call`, що й продакшн: промт,
інжекція каталогу і JSON-режим повністю збігаються.

## Очікувані результати (цільові пороги)

| Модель | Ціль `intent_acc` | Ціль `json_valid` | Ціль p95 |
|---|---|---|---|
| `qwen2.5:1.5b` (типова) | ≥ 85% | ≥ 95% | ≤ 800 мс |
| `qwen2.5:3b` | ≥ 92% | 100% | ≤ 1200 мс |
| `qwen2.5:0.5b` (для малої RAM) | ≥ 70% | ≥ 90% | ≤ 400 мс |
| `phi3-mini:3.8b` | порівняльно — лишити, якщо випереджає `1.5b` |
| `gemma3:1b` | позначається "not recommended", якщо `json_valid < 80%` |

Правило ship-gate: продакшн-модель (`voice.llm_model` у `core.yaml`) має
відповідати цільовому рядку свого класу розміру.

## Корпус

`intent_corpus.jsonl` — 32 кейси, що покривають усі простори імен:

- `device.on` / `device.off` / `device.set_level` / `device.set_temperature`
- `device.query_temperature` / `device.query_state`
- `device.lock` / `device.unlock`
- `media.*` (play_genre, pause, stop, resume, next, volume_up, whats_playing)
- `weather.current` / `weather.forecast`
- `clock.set_alarm` / `clock.set_timer` / `clock.stop_alarm`
- `privacy_on` / `privacy_off`
- `automation.run`
- `presence.who_home`
- `chat` (вільні запитання)

Усі кейси — англійською. Цей бенчмарк перевіряє шлях **після** перекладача.
Обробка інших мов тестується окремо через Argos round-trip.

## Додавання кейсів

Один JSON-об'єкт на рядок:

```json
{"text":"turn on the light","expected":{"intent":"device.on","params":{"entity":"light"}}}
```

Тільки рядок інтенту має збігатися точно; params звіряються регістро-незалежно
по ключах, ключі що не перелічені в `expected.params` ігноруються.
