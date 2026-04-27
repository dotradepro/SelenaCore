import { useWidgetData } from '../../../hooks/useWidgetData';
import { StatusSkeleton } from './Skeleton';
import type { TemplateProps } from './registry';

/** Payload shape — see docs/dashboard-recraft.md §3.3.5. */
export interface StatusPayload {
  label: string;
  pill: {
    tone: 'ok' | 'info' | 'warn' | 'alert' | 'neutral';
    text: string;
    icon?: 'check' | 'clock' | 'alert' | 'x' | 'refresh';
  };
  rows?: { label: string; value: string }[];
}

const TONE_COLOR: Record<StatusPayload['pill']['tone'], string> = {
  ok: 'var(--gr)',
  info: 'var(--ac)',
  warn: 'var(--am)',
  alert: 'var(--rd)',
  neutral: 'var(--tx2)',
};

const ICON_GLYPH: Record<NonNullable<StatusPayload['pill']['icon']>, string> = {
  check: '✓',
  clock: '◷',
  alert: '!',
  x: '✕',
  refresh: '↻',
};

export default function StatusTemplate({ mod }: TemplateProps) {
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

  const tone = TONE_COLOR[data.pill.tone];
  const glyph = data.pill.icon ? ICON_GLYPH[data.pill.icon] : null;

  return (
    <div style={{
      width: '100%', height: '100%',
      padding: '10px 14px 12px',
      display: 'flex', flexDirection: 'column', gap: 6,
    }}>
      <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--tx3)', textTransform: 'uppercase', letterSpacing: '.05em' }}>
        {data.label}
      </div>
      <span style={{
        alignSelf: 'flex-start',
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '3px 9px',
        borderRadius: 999,
        fontSize: 10.5, fontWeight: 500,
        color: tone,
        background: 'color-mix(in srgb, var(--sf2) 70%, transparent)',
        border: `1px solid ${tone}`,
      }}>
        {glyph && <span aria-hidden style={{ fontSize: 11, lineHeight: 1 }}>{glyph}</span>}
        {data.pill.text}
      </span>
      {data.rows && data.rows.length > 0 && (
        <div style={{ marginTop: 4, display: 'flex', flexDirection: 'column', gap: 2 }}>
          {data.rows.slice(0, 4).map((r, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10 }}>
              <span style={{ color: 'var(--tx3)' }}>{r.label}</span>
              <span style={{ color: 'var(--tx2)' }}>{r.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ErrorBlock({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
      <div style={{ fontSize: 11, color: 'var(--rd)' }}>Unavailable</div>
      <button
        onClick={onRetry}
        title={message}
        style={{ fontSize: 10, padding: '3px 10px', borderRadius: 6, background: 'var(--sf2)', color: 'var(--tx2)', border: '1px solid var(--b)', cursor: 'pointer' }}
      >Retry</button>
    </div>
  );
}
