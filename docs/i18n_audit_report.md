# i18n Audit Report

Snapshot of probable hardcoded UI strings in the SPA, produced by
`scripts/i18n_audit.py`. This is a heuristic scan — every entry is a
**candidate** for review, not a guaranteed bug. Brand names and technical
identifiers that must stay unlocalized are expected to appear here; the
point is to track them explicitly rather than let real drift hide among
them.

To regenerate: `python scripts/i18n_audit.py`
To enforce in CI: `python scripts/i18n_lint.py` (compares against
`docs/i18n_audit_baseline.json`).

## Legend

| Kind | Meaning |
|------|---------|
| `text` | JSX text node content (`<tag>Text</tag>`) |
| `attr` | `placeholder` / `title` / `aria-label` / `alt` / `label` / `tooltip` attribute |

## Disposition (as of plan baseline)

| Count | Category | Action |
|-------|----------|--------|
| ~4    | Brand names (OpenAI, Anthropic, Groq, Google, Emoji, Promise, Record) | **Keep as-is** — glossary pins these. `Promise` / `Record` are false positives (TypeScript generic names in JSDoc-like comments). |
| ~6    | Accessibility attrs (`title="Remove"`, `title="Close"`, `title="Details"`, `aria-label="Settings"`) | **Migrate** opportunistically. Low-visibility but real translatable strings. |
| ~2    | Placeholders (`placeholder="new PIN"`, `placeholder="Setup QR code"`) | **Migrate**. User-visible input hints. |
| ~1    | Marketing strings (`"My Theme"`) | **Migrate**. |

Full current baseline count: **15** (frozen in `docs/i18n_audit_baseline.json`).

## Already migrated (v0.4.0 A1)

The following strings identified in the planning audit have been migrated to
`t(...)` calls and keys added to `en.ts` / `uk.ts`:

- `Dashboard.tsx:145` — `"Stopped"` → `dashboard.widgetStopped`
- `IntegrityPage.tsx:82-83` — `"All core files verified"` / `"Integrity violation detected"` → `integrityPage.allVerified` / `integrityPage.violationDetected`
- `IntegrityPage.tsx:85` — `"SHA256 · … checks · every 30s"` → `integrityPage.metaLine` (with `{{checks}}`)
- `IntegrityPage.tsx:88` — `"Last check: …s ago"` → `integrityPage.lastCheck` (with `{{age}}`)
- `IntegrityPage.tsx:103` — `"Check log"` → `integrityPage.checkLog`
- `IntegrityPage.tsx:23-27` — hardcoded log-entry seed text → `integrityPage.logAllOk*`, `logModuleUpdated`, `logCoreStartupVerified`

## Remaining candidates

See `scripts/i18n_audit.py` output for the live list. The baseline gate
prevents new hardcoded strings from sneaking into the codebase; shrinking
the count is opportunistic cleanup.

## Scanner limitations

The regex-based scanner does **not** catch:

- Strings inside `{...}` expressions (ternaries, `.map()` returns, helper
  functions that build labels). Example caught manually:
  `{intOk ? 'All core files verified' : 'Integrity violation detected'}`.
- Strings passed through intermediate variables (`const title = 'Save'; return <button>{title}</button>`).
- Strings in `toast(...)`, `alert(...)`, `console.*` calls (many of these are
  internal debug text — correctly not translatable).

Manual spot-checks are still required when adding new pages.
