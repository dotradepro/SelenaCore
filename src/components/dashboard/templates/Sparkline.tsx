import { useWidgetData } from '../../../hooks/useWidgetData';
import { SparklineSkeleton } from './Skeleton';
import type { TemplateProps } from './registry';

/** Payload shape — see docs/dashboard-recraft.md §3.3.2. */
export interface SparklinePayload {
  label: string;
  value: string;
  unit?: string;
  footnote?: string;
  series: number[];
  series_window_s?: number;
  tone?: 'neutral' | 'info' | 'ok' | 'warn' | 'alert';
}

const TONE_STROKE: Record<NonNullable<SparklinePayload['tone']>, string> = {
  neutral: 'var(--tx2)',
  info: 'var(--ac)',
  ok: 'var(--gr)',
  warn: 'var(--am)',
  alert: 'var(--rd)',
};

export default function SparklineTemplate({ mod }: TemplateProps) {
  const widget = mod.ui?.widget;
  const { data, loading, error, refetch } = useWidgetData<SparklinePayload>({
    module: mod.name,
    key: 'state',
    events: widget?.refresh?.events,
    pollIntervalS: widget?.refresh?.poll_interval_s,
  });

  if (loading && !data) return <SparklineSkeleton />;
  if (error && !data) return <ErrorBlock onRetry={refetch} message={error} />;
  if (!data) return <SparklineSkeleton />;

  const tone = data.tone ?? 'info';
  return (
    <div style={{
      width: '100%', height: '100%',
      padding: '10px 14px 12px',
      display: 'flex', flexDirection: 'column', gap: 4,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--tx3)', textTransform: 'uppercase', letterSpacing: '.05em' }}>
          {data.label}
        </div>
        {data.footnote && (
          <div style={{ fontSize: 9, color: 'var(--tx3)' }}>{data.footnote}</div>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span style={{ fontSize: 22, fontWeight: 600, color: 'var(--tx)', lineHeight: 1 }}>
          {data.value}
        </span>
        {data.unit && (
          <span style={{ fontSize: 11, color: 'var(--tx3)' }}>{data.unit}</span>
        )}
      </div>
      <SparklineSvg series={data.series} stroke={TONE_STROKE[tone]} />
    </div>
  );
}

function SparklineSvg({ series, stroke }: { series: number[]; stroke: string }) {
  if (!series || series.length < 2) {
    return <div style={{ flex: 1, minHeight: 24 }} />;
  }
  const min = Math.min(...series);
  const max = Math.max(...series);
  const padY = (max - min) * 0.08 || 1;
  const yMin = min - padY;
  const yMax = max + padY;

  // viewBox: 0..100 horizontal × 0..32 vertical. The component scales to the
  // available width via preserveAspectRatio="none" so the SVG stretches to
  // fill any cell width without distorting the line stroke perceptually.
  const W = 100;
  const H = 32;
  const stepX = W / (series.length - 1);
  const points = series.map((v, i) => {
    const x = i * stepX;
    const t = (v - yMin) / (yMax - yMin || 1);
    const y = H - t * H;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');

  // Soft fill below the line so the curve still reads on tiny tiles.
  const fillPoints = `0,${H} ${points} ${W},${H}`;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      style={{ width: '100%', flex: 1, minHeight: 22, marginTop: 4, overflow: 'visible' }}
    >
      <polygon points={fillPoints} fill={stroke} fillOpacity={0.12} />
      <polyline points={points} fill="none" stroke={stroke} strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
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
