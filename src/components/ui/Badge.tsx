import type { ReactNode } from 'react';

type Tone = 'on' | 'off' | 'warn' | 'info' | 'pr' | 'neutral';

export interface BadgeProps {
  tone?: Tone;
  className?: string;
  children: ReactNode;
}

const TONE_BG: Record<Tone, string> = {
  on: 'rgba(46,201,138,.12)',
  off: 'rgba(255,255,255,.05)',
  warn: 'rgba(245,169,58,.12)',
  info: 'rgba(79,140,247,.12)',
  pr: 'rgba(155,110,244,.14)',
  neutral: 'rgba(255,255,255,.04)',
};

const TONE_FG: Record<Tone, string> = {
  on: 'var(--gr)',
  off: 'var(--tx3)',
  warn: 'var(--am)',
  info: 'var(--ac)',
  pr: 'var(--pu)',
  neutral: 'var(--tx2)',
};

export function Badge({ tone = 'neutral', className, children }: BadgeProps) {
  return (
    <span
      className={['badge', className].filter(Boolean).join(' ')}
      style={{ background: TONE_BG[tone], color: TONE_FG[tone] }}
    >
      {children}
    </span>
  );
}
