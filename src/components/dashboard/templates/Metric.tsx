import { useWidgetData } from '../../../hooks/useWidgetData';
import { MetricSkeleton } from './Skeleton';
import type { TemplateProps } from './registry';

/** Payload shape — see docs/dashboard-recraft.md §3.3.1. */
export interface MetricPayload {
  label: string;
  value: string;
  unit?: string | null;
  trend?: {
    direction: 'up' | 'down' | 'flat';
    magnitude: string;
    period: string;
  } | null;
  tone?: 'neutral' | 'info' | 'ok' | 'warn' | 'alert';
}

const TONE_COLOR: Record<NonNullable<MetricPayload['tone']>, string> = {
  neutral: 'var(--tx2)',
  info: 'var(--ac)',
  ok: 'var(--gr)',
  warn: 'var(--am)',
  alert: 'var(--rd)',
};

const TREND_GLYPH: Record<'up' | 'down' | 'flat', string> = {
  up: '▲', down: '▼', flat: '→',
};

export default function MetricTemplate({ mod }: TemplateProps) {
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
  return (
    <div style={{
      width: '100%', height: '100%',
      padding: '12px 14px',
      display: 'flex', flexDirection: 'column',
      justifyContent: 'space-between',
    }}>
      <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--tx3)', textTransform: 'uppercase', letterSpacing: '.05em' }}>
        {data.label}
      </div>
      <div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span style={{ fontSize: 28, fontWeight: 600, color: 'var(--tx)', lineHeight: 1 }}>
            {data.value}
          </span>
          {data.unit && (
            <span style={{ fontSize: 11, color: 'var(--tx3)' }}>{data.unit}</span>
          )}
        </div>
        {data.trend && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 6, fontSize: 10, color: TONE_COLOR[tone] }}>
            <span aria-hidden>{TREND_GLYPH[data.trend.direction]}</span>
            <span>{data.trend.magnitude}</span>
            <span style={{ color: 'var(--tx3)' }}>· {data.trend.period}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function ErrorBlock({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div style={{
      width: '100%', height: '100%',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
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
