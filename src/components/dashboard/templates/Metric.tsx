import { motion } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { useWidgetData } from '../../../hooks/useWidgetData';
import { MetricSkeleton } from './Skeleton';
import Icon from './Icon';
import { resolveLabel } from './i18n';
import type { TemplateProps } from './registry';

export interface MetricPayload {
  /** Raw English label, kept as fallback when `label_key` is missing. */
  label: string;
  /** Optional i18n key — convention `widgets.<module>.<slot>`. */
  label_key?: string;
  value: string;
  unit?: string | null;
  trend?: {
    direction: 'up' | 'down' | 'flat';
    magnitude: string;
    period: string;
    /** Optional i18n key for the trend period. Falls back to raw `period`. */
    period_key?: string;
    period_args?: Record<string, string | number>;
  } | null;
  tone?: 'neutral' | 'info' | 'ok' | 'warn' | 'alert';
  icon?: string | null;
}

const TONE_COLOR: Record<NonNullable<MetricPayload['tone']>, string> = {
  neutral: 'var(--tx2)',
  info: 'var(--ac)',
  ok: 'var(--gr)',
  warn: 'var(--am)',
  alert: 'var(--rd)',
};

const TONE_TINT: Record<NonNullable<MetricPayload['tone']>, string> = {
  neutral: 'transparent',
  info: 'radial-gradient(ellipse at 80% -10%, rgba(90,150,255,.10), transparent 60%)',
  ok: 'radial-gradient(ellipse at 80% -10%, rgba(52,214,147,.10), transparent 60%)',
  warn: 'radial-gradient(ellipse at 80% -10%, rgba(245,169,58,.12), transparent 60%)',
  alert: 'radial-gradient(ellipse at 80% -10%, rgba(224,84,84,.14), transparent 60%)',
};

const TREND_GLYPH: Record<'up' | 'down' | 'flat', string> = {
  up: '↑', down: '↓', flat: '→',
};

export default function MetricTemplate({ mod }: TemplateProps) {
  const { t } = useTranslation();
  const widget = mod.ui?.widget;
  const { data, loading, error, refetch } = useWidgetData<MetricPayload>({
    module: mod.name,
    key: 'state',
    events: widget?.refresh?.events,
    pollIntervalS: widget?.refresh?.poll_interval_s,
  });

  if (loading && !data) return <MetricSkeleton />;
  if (error && !data) return <ErrorBlock onRetry={refetch} message={error} />;
  if (!data) return <MetricSkeleton />;

  const tone = data.tone ?? 'neutral';
  const accent = TONE_COLOR[tone];
  const label = resolveLabel(t, data.label, data.label_key);
  const trendPeriod = resolveLabel(
    t, data.trend?.period, data.trend?.period_key, data.trend?.period_args,
  );
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
        justifyContent: 'space-between',
        background: TONE_TINT[tone],
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
        <div style={{
          fontSize: 9.5,
          fontWeight: 600,
          color: 'var(--tx3)',
          textTransform: 'uppercase',
          letterSpacing: '.08em',
        }}>
          {label}
        </div>
        {data.icon && <Icon name={data.icon} size={22} />}
      </div>

      <div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span style={{
            fontSize: 34,
            fontWeight: 600,
            color: 'var(--tx)',
            lineHeight: 1,
            letterSpacing: '-.015em',
            fontVariantNumeric: 'tabular-nums',
          }}>
            {data.value}
          </span>
          {data.unit && (
            <span style={{ fontSize: 11, color: 'var(--tx3)', fontWeight: 500 }}>
              {data.unit}
            </span>
          )}
        </div>
        {data.trend && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            marginTop: 6,
            fontSize: 10,
            color: accent,
            fontVariantNumeric: 'tabular-nums',
          }}>
            <span aria-hidden style={{ fontWeight: 600 }}>{TREND_GLYPH[data.trend.direction]}</span>
            <span style={{ fontWeight: 500 }}>{data.trend.magnitude}</span>
            <span style={{ color: 'var(--tx3)' }}>· {trendPeriod}</span>
          </div>
        )}
      </div>
    </motion.div>
  );
}

function ErrorBlock({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div style={{
      width: '100%', height: '100%', display: 'flex',
      flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      gap: 6, padding: 10,
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
