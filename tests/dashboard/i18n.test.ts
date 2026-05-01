/**
 * Tests for `templates/i18n.ts` — the `resolveLabel` helper used by every
 * widget template to translate payload-supplied label keys with raw
 * English fallback.
 *
 * The helper is a one-line wrapper around i18next's `t()` plus a
 * fallback rule, but the rule has subtle edge cases (missing key,
 * missing raw, both missing, args interpolation) that have already
 * caught regressions. Pinning them here makes future template additions
 * safer.
 */
import { test, describe } from 'vitest';
import assert from 'node:assert/strict';
import { resolveLabel } from '../../src/components/dashboard/templates/i18n';

// Minimal i18next-compatible mock. The real `t()` returns the key when
// not found AND no defaultValue is set; with defaultValue, it returns
// the default. With args, it interpolates `{{name}}` placeholders.
function makeT(bundle: Record<string, string>) {
  return ((key: string, opts?: Record<string, unknown>) => {
    let str = bundle[key];
    if (str === undefined) {
      str = (opts?.defaultValue as string) ?? key;
    }
    if (opts) {
      for (const [k, v] of Object.entries(opts)) {
        if (k === 'defaultValue') continue;
        str = str.replace(new RegExp(`{{\\s*${k}\\s*}}`, 'g'), String(v));
      }
    }
    return str;
  }) as never;  // matches the TFunction structural shape we need
}

describe('resolveLabel', () => {
  test('uses translated key when present', () => {
    const t = makeT({ 'widgets.test.label': 'Translated' });
    assert.equal(resolveLabel(t, 'Raw', 'widgets.test.label'), 'Translated');
  });

  test('falls back to raw English when key is missing in bundle', () => {
    const t = makeT({});
    // i18next returns defaultValue when key absent → resolveLabel propagates.
    assert.equal(resolveLabel(t, 'Raw English', 'widgets.test.label'), 'Raw English');
  });

  test('returns raw directly when no key is supplied', () => {
    const t = makeT({ 'widgets.test.label': 'Translated' });
    assert.equal(resolveLabel(t, 'Plain raw', undefined), 'Plain raw');
    assert.equal(resolveLabel(t, 'Plain raw', null), 'Plain raw');
  });

  test('interpolates args into the translated string', () => {
    const t = makeT({ 'widgets.energy.footnote': 'today · {{kwh}} kWh' });
    const out = resolveLabel(t, 'today · 8.7 kWh', 'widgets.energy.footnote', { kwh: '8.7' });
    assert.equal(out, 'today · 8.7 kWh');
  });

  test('interpolates args even when falling back to defaultValue', () => {
    // Key missing → t() returns defaultValue; interpolation still applies.
    const t = makeT({});
    const out = resolveLabel(t, 'today · {{kwh}} kWh', 'widgets.missing.key', { kwh: '8.7' });
    assert.equal(out, 'today · 8.7 kWh');
  });

  test('handles plural-style numeric args', () => {
    const t = makeT({ 'widgets.presence.lastSeenMinutes': '{{count}}m ago' });
    assert.equal(
      resolveLabel(t, '5m ago', 'widgets.presence.lastSeenMinutes', { count: 5 }),
      '5m ago',
    );
  });

  test('returns empty string when raw and key are both absent', () => {
    const t = makeT({});
    // Templates render `{label}` directly; an empty string keeps JSX
    // valid instead of literal "undefined" leaking into the DOM.
    assert.equal(resolveLabel(t, undefined, undefined), '');
    assert.equal(resolveLabel(t, null, undefined), '');
  });

  test('null/undefined raw with present key still translates', () => {
    const t = makeT({ 'widgets.test.label': 'Translated' });
    assert.equal(resolveLabel(t, null, 'widgets.test.label'), 'Translated');
    assert.equal(resolveLabel(t, undefined, 'widgets.test.label'), 'Translated');
  });

  test('empty-string key is treated as no key (uses raw)', () => {
    // Backends that always emit a `_key` field but leave it empty for
    // un-translated entries shouldn't accidentally call t('').
    const t = makeT({ 'widgets.test.label': 'Translated' });
    assert.equal(resolveLabel(t, 'Raw', ''), 'Raw');
  });
});
