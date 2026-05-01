import { motion } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { useWidgetData } from '../../../hooks/useWidgetData';
import { StatusSkeleton } from './Skeleton';
import Icon from './Icon';
import Pill from './blocks/Pill';
import CardRow, { type CardSpec } from './blocks/CardRow';
import IconStrip, { type IconStripItem } from './blocks/IconStrip';
import ActionButton, { type ActionSpec } from './blocks/ActionButton';
import { resolveLabel } from './i18n';
import type { TemplateProps } from './registry';

export interface StatusPayload {
  /** Raw English label, kept as fallback when `label_key` is missing. */
  label: string;
  label_key?: string;
  pill: {
    tone: 'ok' | 'info' | 'warn' | 'alert' | 'neutral';
    /** Raw English pill text, fallback when `text_key` is missing. */
    text: string;
    text_key?: string;
    text_args?: Record<string, string | number>;
    icon?: string;
  };
  rows?: {
    label: string;
    label_key?: string;
    value: string;
    value_key?: string;
    value_args?: Record<string, string | number>;
    icon?: string;
  }[];
  cards?: CardSpec[];
  strip?: IconStripItem[];
  actions?: ActionSpec[];
}

const TONE_TINT: Record<StatusPayload['pill']['tone'], string> = {
  neutral: 'transparent',
  info: 'radial-gradient(ellipse at 80% -10%, rgba(90,150,255,.10), transparent 60%)',
  ok: 'radial-gradient(ellipse at 80% -10%, rgba(52,214,147,.10), transparent 60%)',
  warn: 'radial-gradient(ellipse at 80% -10%, rgba(245,169,58,.12), transparent 60%)',
  alert: 'radial-gradient(ellipse at 80% -10%, rgba(224,84,84,.14), transparent 60%)',
};

export default function StatusTemplate({ mod }: TemplateProps) {
  const { t } = useTranslation();
  const widget = mod.ui?.widget;
  const { data, loading, error, refetch } = useWidgetData<StatusPayload>({
    module: mod.name,
    key: 'state',
    events: widget?.refresh?.events,
    pollIntervalS: widget?.refresh?.poll_interval_s,
  });

  if (loading && !data) return <StatusSkeleton />;
  if (error && !data) return <ErrorBlock onRetry={refetch} message={error} />;
  if (!data) return <StatusSkeleton />;

  const label = resolveLabel(t, data.label, data.label_key);
  const pillText = resolveLabel(t, data.pill.text, data.pill.text_key, data.pill.text_args);

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      style={{
        width: '100%',
        height: '100%',
        padding: '12px 14px',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        background: TONE_TINT[data.pill.tone],
      }}
    >
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: 8,
      }}>
        <div style={{
          fontSize: 9.5,
          fontWeight: 600,
          color: 'var(--tx3)',
          textTransform: 'uppercase',
          letterSpacing: '.08em',
        }}>
          {label}
        </div>
        {data.actions && data.actions.length > 0 && (
          <div style={{ display: 'flex', gap: 4 }}>
            {data.actions.slice(0, 2).map((a) => (
              <ActionButton key={a.id} module={mod.name} spec={a} onSuccess={refetch} />
            ))}
          </div>
        )}
      </div>

      <Pill
        tone={data.pill.tone}
        text={pillText}
        icon={data.pill.icon}
        emphasis
        style={{ alignSelf: 'flex-start' }}
      />

      {data.strip && data.strip.length > 0 && <IconStrip items={data.strip} />}

      {data.rows && data.rows.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3, marginTop: 2 }}>
          {data.rows.slice(0, 4).map((r, i) => (
            <div key={i} style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              fontSize: 10.5,
              gap: 8,
              padding: '2px 0',
              borderBottom: i < data.rows!.slice(0, 4).length - 1 ? '1px solid color-mix(in srgb, var(--b) 60%, transparent)' : 'none',
            }}>
              <span style={{
                color: 'var(--tx3)',
                display: 'flex',
                alignItems: 'center',
                gap: 5,
              }}>
                {r.icon && <Icon name={r.icon} size={11} />}
                {resolveLabel(t, r.label, r.label_key)}
              </span>
              <span style={{ color: 'var(--tx)', fontWeight: 500, fontVariantNumeric: 'tabular-nums' }}>
                {resolveLabel(t, r.value, r.value_key, r.value_args)}
              </span>
            </div>
          ))}
        </div>
      )}

      {data.cards && data.cards.length > 0 && (
        <div style={{ marginTop: 'auto' }}>
          <CardRow cards={data.cards} />
        </div>
      )}
    </motion.div>
  );
}

function ErrorBlock({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div style={{
      width: '100%', height: '100%', display: 'flex',
      flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      gap: 6,
    }}>
      <div style={{ fontSize: 11, color: 'var(--rd)' }}>Unavailable</div>
      <button
        onClick={onRetry}
        title={message}
        style={{
          fontSize: 10, padding: '3px 10px', borderRadius: 6,
          background: 'var(--sf2)', color: 'var(--tx2)',
          border: '1px solid var(--b)', cursor: 'pointer',
        }}
      >Retry</button>
    </div>
  );
}
