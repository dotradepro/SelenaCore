import type { CSSProperties } from 'react';
import Icon from '../Icon';

export type PillTone = 'ok' | 'info' | 'warn' | 'alert' | 'neutral';

export interface PillSpec {
  tone?: PillTone;
  text: string;
  icon?: string | null;
}

const TONE_COLOR: Record<PillTone, string> = {
  ok: 'var(--gr)',
  info: 'var(--ac)',
  warn: 'var(--am)',
  alert: 'var(--rd)',
  neutral: 'var(--tx2)',
};

export interface PillProps {
  tone?: PillTone;
  text: string;
  icon?: string | null;
  /** Slightly larger pill — used for hero region of the weather template. */
  emphasis?: boolean;
  style?: CSSProperties;
}

/** Reusable rounded indicator with optional leading icon. Used by Status,
 *  Weather, and any template that needs an at-a-glance state badge. */
export default function Pill({ tone = 'neutral', text, icon, emphasis, style }: PillProps) {
  const color = TONE_COLOR[tone];
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        padding: emphasis ? '4px 11px' : '3px 9px',
        borderRadius: 999,
        fontSize: emphasis ? 11 : 10.5,
        fontWeight: 500,
        color,
        background: 'color-mix(in srgb, var(--sf2) 70%, transparent)',
        border: `1px solid ${color}`,
        whiteSpace: 'nowrap',
        ...style,
      }}
    >
      {icon && <Icon name={icon} size={emphasis ? 12 : 11} color={color} />}
      {text}
    </span>
  );
}
