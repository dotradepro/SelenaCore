# Внесок у SelenaCore

Дякуємо за інтерес до проєкту!

🇬🇧 [English version](../../CONTRIBUTING.md)

## Робочий процес

1. **Issues first** — будь-яка робота починається зі створення Issue на GitHub
2. **Одне завдання за раз** — берете Issue → реалізуєте → коміт → закриваєте
3. **Тести обов'язкові** — не можна пушити в `main` з тестами, що падають

## Гілки

- Зміни до 200 рядків — робота прямо в `main`
- Більше 200 рядків — `feat/<issue-number>-<slug>`

```bash
git checkout -b feat/5-device-registry
# ... працюєте ...
git checkout main
git merge feat/5-device-registry
git push origin main
```

## Коміти

Формат: `<type>(<scope>): <опис> [#<N>]`

```bash
# Приклади
git commit -m "feat(registry): add Device Registry CRUD with state history [#5]"
git commit -m "fix(agent): handle missing manifest file on first init [#12]"
git commit -m "test(registry): add pytest for state_changed event emission [#68]"
```

Типи: `feat`, `fix`, `chore`, `refactor`, `test`, `docs`, `security`, `perf`

**Заборонено:** `fix`, `update`, `wip`, `.`, порожнє повідомлення.

## Код

- Python 3.11+, усі публічні методи — `async def`
- Типізація обов'язкова (type hints)
- `logging.getLogger(__name__)` — жодних `print()`
- `except Exception as e:` — ніколи порожнього `except: pass`
- Один файл = одна відповідальність

## Тести

```bash
pytest tests/ -v
pytest tests/ --cov=core --cov-report=term-missing
```

Перед кожним push:

```bash
pytest tests/ -x -q           # всі тести зелені
python -m mypy core/           # типізація
```

## Безпека

Якщо знайшли вразливість — **не створюйте публічний Issue**. Напишіть на security@selenehome.tech або через [GitHub Security Advisories](https://github.com/dotradepro/SelenaCore/security/advisories).

## Заборонено

- `eval()`, `exec()` в будь-якому коді
- `shell=True` без крайньої необхідності
- Секрети в `.env` (лише `.env.example`)
- Пряме читання `/secure/` з модуля
- Публікація `core.*` подій з модуля
- Біометрія у вихідних HTTP запитах

## Ліцензія

Усі внески приймаються під ліцензією MIT.
