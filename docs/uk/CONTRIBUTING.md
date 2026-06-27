# Внесок у SelenaCore

Дякуємо за інтерес до проєкту!

[English version](../../CONTRIBUTING.md)

## Робочий процес

1. **Спочатку Issue** — вся робота починається зі створення GitHub Issue
2. **Одне завдання за раз** — берете Issue, реалізуєте, комітите, закриваєте
3. **Тести обов'язкові** — не можна пушити в `main` з тестами, що не проходять

## Гілки

- Зміни до 200 рядків — працюйте безпосередньо в `main`
- Більше 200 рядків — `feat/<issue-number>-<slug>`

```bash
git checkout -b feat/5-device-registry
# ... work ...
git checkout main
git merge feat/5-device-registry
git push origin main
```

## Коміти

Формат: `<type>(<scope>): <description> [#<N>]`

```bash
# Examples
git commit -m "feat(registry): add Device Registry CRUD with state history [#5]"
git commit -m "fix(agent): handle missing manifest file on first init [#12]"
git commit -m "test(registry): add pytest for state_changed event emission [#68]"
```

Типи: `feat`, `fix`, `chore`, `refactor`, `test`, `docs`, `security`, `perf`

**Заборонено:** загальні повідомлення на кшталт `fix`, `update`, `wip`, `.` або порожні повідомлення.

## Стандарти коду

- Python 3.11+, усі публічні методи — `async def`
- Анотації типів обов'язкові для всіх сигнатур функцій
- `logging.getLogger(__name__)` — ніяких `print()`
- `except Exception as e:` — ніколи голий `except: pass`
- Один файл = одна відповідальність
- FastAPI-роутери: лише парсинг HTTP, вся логіка в сервісах
- Pydantic-моделі для всіх схем запитів/відповідей

## Розробка модулів

- Користувацькі модулі взаємодіють лише через **WebSocket Module Bus**
- Базовий клас: `SmartHomeModule` з `sdk/base_module.py`
- Використовуйте декоратори: `@intent()`, `@on_event()`, `@scheduled()`
- Системні модулі успадковують `SystemModule` з `core/module_loader/system_module.py`
- Див. [Посібник з розробки модулів](docs/module-development.md) та [Посібник з системних модулів](docs/system-module-development.md)

## Тести

```bash
pytest tests/ -v
pytest tests/ --cov=core --cov-report=term-missing
```

Перед кожним пушем:

```bash
pytest tests/ -x -q                          # all tests green
python -m mypy core/ --ignore-missing         # type checking
```

## Безпека

Якщо ви знайшли вразливість — **не створюйте публічний Issue**. Використовуйте [GitHub Security Advisories](https://github.com/dotradepro/SelenaCore/security/advisories).

## Заборонені практики

- `eval()`, `exec()` у будь-якому коді
- `os.system()`, `subprocess.run(shell=True)` без крайньої необхідності
- Секрети у `.env` (лише `.env.example`)
- Пряме читання `/secure/` з модуля
- Публікація подій `core.*` з модуля
- Біометричні дані у вихідних HTTP-запитах
- Голий `raise Exception()` — використовуйте власні класи винятків

## Ліцензія

Усі внески приймаються на умовах ліцензії MIT.
