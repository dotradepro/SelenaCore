# Contributing to SelenaCore

Спасибо за интерес к проекту!

## Рабочий процесс

1. **Issues first** — любая работа начинается с создания Issue на GitHub
2. **Одна задача за раз** — берёшь Issue → реализуешь → коммит → закрываешь
3. **Тесты обязательны** — нельзя пушить в `main` с падающими тестами

## Ветки

- Изменения до 200 строк — работа прямо в `main`
- Больше 200 строк — `feat/<issue-number>-<slug>`

```bash
git checkout -b feat/5-device-registry
# ... работаешь ...
git checkout main
git merge feat/5-device-registry
git push origin main
```

## Коммиты

Формат: `<type>(<scope>): <описание> [#<N>]`

```bash
# Примеры
git commit -m "feat(registry): add Device Registry CRUD with state history [#5]"
git commit -m "fix(agent): handle missing manifest file on first init [#12]"
git commit -m "test(registry): add pytest for state_changed event emission [#68]"
```

Типы: `feat`, `fix`, `chore`, `refactor`, `test`, `docs`, `security`, `perf`

**Запрещено:** `fix`, `update`, `wip`, `.`, пустое сообщение.

## Код

- Python 3.11+, все публичные методы — `async def`
- Типизация обязательна (type hints)
- `logging.getLogger(__name__)` — никаких `print()`
- `except Exception as e:` — никогда пустого `except: pass`
- Один файл = одна ответственность

## Тесты

```bash
pytest tests/ -v
pytest tests/ --cov=core --cov-report=term-missing
```

Перед каждым push:

```bash
pytest tests/ -x -q           # все тесты зелёные
python -m mypy core/           # типизация
```

## Безопасность

Если нашёл уязвимость — **не создавай публичный Issue**. Напиши на security@selenehome.tech или через [GitHub Security Advisories](https://github.com/dotradepro/SelenaCore/security/advisories).

## Запрещено

- `eval()`, `exec()` в любом коде
- `shell=True` без крайней необходимости
- Секреты в `.env` (только `.env.example`)
- Прямое чтение `/secure/` из модуля
- Публикация `core.*` событий из модуля
- Биометрия в исходящих HTTP запросах

## Лицензия

Все вклады принимаются под лицензией MIT.
