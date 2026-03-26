# Contributing to SelenaCore

Thank you for your interest in the project!

🇺🇦 [Українська версія](docs/uk/CONTRIBUTING.md)

## Workflow

1. **Issues first** — all work starts with creating a GitHub Issue
2. **One task at a time** — take an Issue → implement → commit → close
3. **Tests required** — cannot push to `main` with failing tests

## Branches

- Changes under 200 lines — work directly in `main`
- Over 200 lines — `feat/<issue-number>-<slug>`

```bash
git checkout -b feat/5-device-registry
# ... work ...
git checkout main
git merge feat/5-device-registry
git push origin main
```

## Commits

Format: `<type>(<scope>): <description> [#<N>]`

```bash
# Examples
git commit -m "feat(registry): add Device Registry CRUD with state history [#5]"
git commit -m "fix(agent): handle missing manifest file on first init [#12]"
git commit -m "test(registry): add pytest for state_changed event emission [#68]"
```

Types: `feat`, `fix`, `chore`, `refactor`, `test`, `docs`, `security`, `perf`

**Forbidden:** `fix`, `update`, `wip`, `.`, empty message.

## Code

- Python 3.11+, all public methods — `async def`
- Type hints required
- `logging.getLogger(__name__)` — no `print()`
- `except Exception as e:` — never bare `except: pass`
- One file = one responsibility

## Tests

```bash
pytest tests/ -v
pytest tests/ --cov=core --cov-report=term-missing
```

Before each push:

```bash
pytest tests/ -x -q                          # all tests green
python -m mypy core/ --ignore-missing         # type checking
```

## Security

If you find a vulnerability — **do not create a public Issue**. Use [GitHub Security Advisories](https://github.com/dotradepro/SelenaCore/security/advisories).

## Forbidden

- `eval()`, `exec()` in any code
- `shell=True` without absolute necessity
- Secrets in `.env` (only `.env.example`)
- Direct reading of `/secure/` from a module
- Publishing `core.*` events from a module
- Biometrics in outgoing HTTP requests

## License

All contributions are accepted under the MIT license.
