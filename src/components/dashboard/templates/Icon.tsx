import type { CSSProperties } from 'react';

/** Emoji-first widget icons. The dashboard renders glyphs from the system
 *  emoji font instead of bundling lucide-react SVGs — modules ship the
 *  same string ``name`` (``cloud`` / ``droplets`` / ...) and we look up
 *  a Unicode glyph here. Keeps bundle size minimal, leverages the OS
 *  font (Apple Color Emoji / Segoe UI Emoji / Noto Color Emoji) and
 *  matches the visual feel of the legacy widget.html files. */
const EMOJI_MAP: Record<string, string> = {
  // Status
  'activity': '📊',
  'alert': '⚠️',
  'alert-circle': '⚠️',
  'alert-triangle': '⚠️',
  'bell': '🔔',
  'check': '✓',
  'check-circle': '✅',
  'clock': '🕒',
  'eye': '👁️',
  'refresh': '🔄',
  'refresh-cw': '🔄',
  'shield': '🛡️',
  'sparkles': '✨',
  'x': '✕',

  // Weather
  'cloud': '☁️',
  'cloud-rain': '🌧️',
  'cloud-snow': '🌨️',
  'sun': '☀️',
  'moon': '🌙',
  'wind': '💨',
  'droplets': '💧',
  'snowflake': '❄️',
  'thermometer': '🌡️',

  // Devices / control
  'lightbulb': '💡',
  'power': '🔌',
  'zap': '⚡',
  'tv': '📺',
  'radio': '📻',
  'music': '🎵',
  'volume-2': '🔊',
  'mic': '🎙️',

  // Network / system
  'cpu': '🖥️',
  'server': '🗄️',
  'globe': '🌍',
  'network': '🔗',
  'wifi': '📶',
  'bluetooth': '🔵',
  'satellite': '📡',
  'settings': '⚙️',

  // People
  'user': '👤',
  'user-check': '🟢',
  'user-x': '🔴',
  'home': '🏠',

  // Other
  'alarm-clock': '⏰',
  'calendar': '📅',
  'workflow': '🔁',
  'chevron-right': '›',
};

export interface IconProps {
  name?: string | null;
  /** Pixel size — emoji renders at this CSS font-size. Default 16. */
  size?: number;
  /** Optional CSS color — only applies to monochrome glyphs (✓, ✕). The
   *  colored emoji above ignore color and render in their native palette. */
  color?: string;
  style?: CSSProperties;
  className?: string;
  /** Override fallback when name isn't recognised. Defaults to the raw
   *  ``name`` so a payload typo at least shows the intended hint. */
  fallback?: string;
}

/** Resolve a payload icon name to an emoji glyph wrapped in a span sized
 *  to match the requested ``size``. Returns ``null`` only when both
 *  ``name`` and ``fallback`` are absent. */
export default function Icon({
  name,
  size = 16,
  color,
  style,
  className,
  fallback,
}: IconProps) {
  const glyph = resolveGlyph(name, fallback);
  if (!glyph) return null;
  return (
    <span
      aria-hidden
      className={className}
      style={{
        fontSize: size,
        lineHeight: 1,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily:
          '"Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", "Twemoji Mozilla", sans-serif',
        color,
        ...style,
      }}
    >
      {glyph}
    </span>
  );
}

function resolveGlyph(name?: string | null, fallback?: string): string | null {
  if (name) {
    const hit = EMOJI_MAP[name.toLowerCase()];
    if (hit) return hit;
  }
  if (fallback && fallback.trim()) return fallback;
  return name ?? null;
}

export function isKnownIcon(name?: string | null): boolean {
  if (!name) return false;
  return name.toLowerCase() in EMOJI_MAP;
}
