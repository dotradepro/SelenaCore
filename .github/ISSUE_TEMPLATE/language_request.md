---
name: Language request
about: Request a new UI language for SelenaCore
title: "[Language] "
labels: i18n, language-request
---

## Language requested

- **Native name:** (e.g. Ελληνικά)
- **English name:** (e.g. Greek)
- **ISO 639-1 code:** (e.g. el)
- **Text direction:** (LTR / RTL)

## Why this language?

Briefly — who speaks it, why the SelenaCore audience would benefit.
Rough speaker count or deployment context is useful.

## Community commitment (optional)

Adding a language has two phases:

1. **Auto-generation** — one of the maintainers adds the code to
   `scripts/i18n_config.py` and the next CI run produces a
   machine-translated `{code}.auto.json`. Quality varies per language;
   sometimes it's good enough on its own, sometimes it needs polish.

2. **Community polish** — native speakers drop a
   `src/i18n/locales/{code}.community.json` with improvements. See
   [CONTRIBUTING_i18n.md](../../docs/CONTRIBUTING_i18n.md).

If you're a native speaker and willing to polish the auto-generated
bundle after it lands, mention it here — it accelerates the roadmap.

## Argos package availability

Check whether an en→{code} Argos package exists at
<https://www.argosopentech.com/argospm/index/>. If not, we may need a
Helsinki-NLP fallback — note that here.

## Additional context

Any notes on regional variants (e.g. pt vs pt-BR), formal/informal
conventions, or UI conventions we should know about.
