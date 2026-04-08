## Summary

Closes #

Briefly describe the change and the motivation behind it.

## Type of change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to change)
- [ ] Refactor (no behaviour change)
- [ ] Documentation update
- [ ] Tests
- [ ] Chore / infrastructure

## Checklist

- [ ] `pytest tests/ -x -q` passes locally
- [ ] `python -m mypy core/ --ignore-missing` is clean for changed files
- [ ] All new functions have type hints
- [ ] No `print()` calls — only `logging.getLogger(__name__)`
- [ ] No secrets, tokens, or `.env` files committed
- [ ] Documentation updated if behaviour changed
- [ ] EN and UK i18n keys updated together (if user-facing strings changed)
- [ ] Conventional Commit message: `<type>(<scope>): <description> [#<issue>]`

## Testing

How did you test this change? Include commands run, hardware used, and manual steps.

## Screenshots / logs

If the change is visual or affects log output, attach screenshots or log snippets.
