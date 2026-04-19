---
name: Translation improvement
about: Report bad / funny / inaccurate translations in an auto-generated language
title: "[i18n] "
labels: i18n, translation-improvement
---

## Language

- **Code:** (e.g. pl, ru, ja)
- **Native name:** (e.g. Polski)

## What's wrong

List the keys and the problems. Paste the wrong translation, the correct
one, and why the wrong one is wrong if it's not obvious.

```
dashboard.welcomeHome
  current:  "Witamy w domu."      ← too formal for a smart-home kiosk
  suggested: "Witaj w domu"
```

```
integrityPage.metaLine
  current:  "SHA256 ·{{checks}}kontrole · co 30 lat"    ← "lat" = years 😬
  suggested: "SHA256 · {{checks}} kontroli · co 30 s"
  why:      Argos translated "s" as the abbreviation for "years"
```

## Scope

- [ ] Just the keys listed above
- [ ] I can do a broader pass and submit a PR with a
      `src/i18n/locales/{code}.community.json`

See [docs/CONTRIBUTING_i18n.md](../../docs/CONTRIBUTING_i18n.md) for the
community-override workflow. You don't need to translate everything —
any keys in `.community.json` take priority over the auto-generated
tier; the rest keep their auto translations.

## Additional context

Regional conventions, preferred register (formal / casual / neutral),
anything else that helps us judge the suggestion.
