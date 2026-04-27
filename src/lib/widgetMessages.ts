/**
 * Typed postMessage protocol between the parent SPA and custom widget iframes.
 *
 * See docs/dashboard-recraft.md §3.4. Phase 5 removed the deprecation
 * aliases for ``openWidgetModal``, ``closeWidgetModal``, ``openSettings``
 * and ``refresh`` — custom widgets must use the canonical names listed in
 * the ``WidgetMessage`` union below.
 */

export type WidgetMessage =
  | { type: 'ready' }
  | { type: 'modal_open'; module: string; width?: number; height?: number }
  | { type: 'modal_close'; module: string }
  | { type: 'modal_resize'; width: number; height: number }
  | { type: 'open_settings'; module: string }
  | { type: 'request_refresh' }
  | { type: 'theme_changed'; theme: 'dark' | 'light' };

/** Normalize a raw message coming over postMessage into a typed
 *  ``WidgetMessage``. Returns ``null`` for shapes that don't belong to the
 *  protocol so the caller can ignore them. */
export function normalizeWidgetMessage(raw: unknown): WidgetMessage | null {
  if (!raw || typeof raw !== 'object') return null;
  const obj = raw as Record<string, unknown>;
  const rawType = obj.type;
  if (typeof rawType !== 'string' || !KNOWN_TYPES.has(rawType)) return null;
  const canonicalType = rawType as WidgetMessage['type'];

  switch (canonicalType) {
    case 'ready':
      return { type: 'ready' };
    case 'modal_open': {
      const m = obj.module;
      if (typeof m !== 'string') return null;
      return {
        type: 'modal_open',
        module: m,
        width: typeof obj.width === 'number' ? obj.width : undefined,
        height: typeof obj.height === 'number' ? obj.height : undefined,
      };
    }
    case 'modal_close': {
      const m = obj.module;
      return { type: 'modal_close', module: typeof m === 'string' ? m : '' };
    }
    case 'modal_resize': {
      const w = obj.width;
      const h = obj.height;
      if (typeof w !== 'number' || typeof h !== 'number') return null;
      return { type: 'modal_resize', width: w, height: h };
    }
    case 'open_settings': {
      const m = obj.module;
      if (typeof m !== 'string') return null;
      return { type: 'open_settings', module: m };
    }
    case 'request_refresh':
      return { type: 'request_refresh' };
    case 'theme_changed': {
      const theme = obj.theme;
      if (theme !== 'dark' && theme !== 'light') return null;
      return { type: 'theme_changed', theme };
    }
  }
}

const KNOWN_TYPES: Set<string> = new Set([
  'ready',
  'modal_open',
  'modal_close',
  'modal_resize',
  'open_settings',
  'request_refresh',
  'theme_changed',
]);

// ── Design-token injection ─────────────────────────────────────────────────

/** CSS custom properties we mirror into custom widget iframes. Stays in
 *  sync with `src/index.css` and is intentionally explicit (not derived
 *  via `getComputedStyle` iteration) so a widget never sees an empty token
 *  list when the parent stylesheet hasn't fully loaded yet. */
const TOKEN_NAMES: readonly string[] = [
  '--bg',
  '--bg-gradient-start',
  '--bg-gradient-end',
  '--sf', '--sf2', '--sf3',
  '--b', '--b2',
  '--tx', '--tx2', '--tx3',
  '--ac', '--gr', '--am', '--rd', '--pu', '--tl',
  '--on-accent', '--on-success', '--on-warning', '--on-danger',
  '--topbar', '--r', '--shadow', '--shadow-lg',
  '--ws-bg', '--ws-blur',
  // Phase 1 V2 tokens
  '--hero-tint', '--widget-glow-on', '--motion-spring', '--skeleton-bg',
];

function readTokensFromRoot(): string {
  const cs = getComputedStyle(document.documentElement);
  const decls: string[] = [];
  for (const name of TOKEN_NAMES) {
    const v = cs.getPropertyValue(name).trim();
    if (v) decls.push(`${name}:${v}`);
  }
  return decls.join(';');
}

/** Write a `<style id="__selena_tokens">:root{...}</style>` block into a
 *  single iframe. Re-injects on every call so theme changes propagate.
 *  Cross-origin iframes silently fail. */
export function injectTokensIntoIframe(iframe: HTMLIFrameElement): void {
  try {
    const doc = iframe.contentDocument;
    if (!doc?.head) return;
    const css = `:root{${readTokensFromRoot()}}`;
    let style = doc.getElementById('__selena_tokens') as HTMLStyleElement | null;
    if (!style) {
      style = doc.createElement('style');
      style.id = '__selena_tokens';
      doc.head.appendChild(style);
    }
    style.textContent = css;
  } catch {
    /* cross-origin or document not yet ready */
  }
}

/** Bulk-inject tokens into every iframe in the document. Used by the
 *  global theme switcher so both the existing V1 widget iframes and any
 *  custom V2 iframes pick up the new theme without a manual reload. */
export function injectTokensIntoAllIframes(): void {
  document.querySelectorAll<HTMLIFrameElement>('iframe').forEach(injectTokensIntoIframe);
}
