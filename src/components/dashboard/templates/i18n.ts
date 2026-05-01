import type { TFunction } from 'i18next';

/** Optional interpolation params accepted by ``resolveLabel``. Mirrors
 *  the shape backend modules emit on payload fields like
 *  ``summary_args``, ``trend.period_args``, ``label_args`` — keys are
 *  arbitrary names referenced via ``{{name}}`` in the i18n string. */
export type LabelArgs = Record<string, string | number>;

/** Resolve a payload-supplied label through i18next, with the raw
 *  English string as fallback when the key is missing or absent.
 *
 *  Backends emit two parallel fields for every localized slot:
 *  - ``label`` (raw English, always present) — kept so SDK clients
 *    that ignore i18n still render a sensible string.
 *  - ``label_key`` (optional translation key) — points to the bundle
 *    entry the dashboard's i18next instance will resolve.
 *
 *  This helper centralises the resolution rule used by all five
 *  template renderers (Metric, Status, Sparkline, ControlPanel,
 *  ToggleList): "if a key is set, hand it to ``t()`` with ``raw`` as
 *  ``defaultValue`` and ``args`` as interpolation params; otherwise
 *  return ``raw`` directly". Cuts down on five duplicate ternaries.
 *
 *  Returns ``''`` when both inputs are absent, since templates render
 *  ``{label}`` directly into JSX. */
export function resolveLabel(
  t: TFunction,
  raw: string | null | undefined,
  key?: string | null,
  args?: LabelArgs,
): string {
  if (key) {
    return t(key, { ...(args ?? {}), defaultValue: raw ?? '' });
  }
  return raw ?? '';
}
