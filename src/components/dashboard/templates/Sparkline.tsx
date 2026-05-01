import { motion } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { useWidgetData } from '../../../hooks/useWidgetData';
import { SparklineSkeleton } from './Skeleton';
import Icon from './Icon';
import CardRow, { type CardSpec } from './blocks/CardRow';
import { resolveLabel } from './i18n';
import type { TemplateProps } from './registry';

export interface SparklinePayload {
  /** Raw English label, kept as fallback when `label_key` is missing. */
  label: string;
  label_key?: string;
  value: string;
  unit?: string;
  /** Raw English footnote (e.g. "today · 8.7 kWh"), fallback for footnote_key. */
  footnote?: string;
  footnote_key?: string;
  footnote_args?: Record<string, string | number>;
  series: number[];
  series_window_s?: number;
  tone?: 'neutral' | 'info' | 'ok' | 'warn' | 'alert';
  icon?: string | null;
  breakdown?: CardSpec[];
}

const TONE_STROKE: Record<NonNullable<SparklinePayload['tone']>, string> = {
  neutral: 'var(--tx2)',
  info: 'var(--ac)',
  ok: 'var(--gr)',
  warn: 'var(--am)',
  alert: 'var(--rd)',
};

const TONE_RGB: Record<NonNullable<SparklinePayload['tone']>, string> = {
  neutral: '160,165,190',
  info: '90,150,255',
  ok: '52,214,147',
  warn: '245,169,58',
  alert: '224,84,84',
};

export default function SparklineTemplate({ mod }: TemplateProps) {
  const { t } = useTranslation();
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
  const stroke = TONE_STROKE[tone];
  const rgb = TONE_RGB[tone];
  const label = resolveLabel(t, data.label, data.label_key);
  const footnote = resolveLabel(t, data.footnote, data.footnote_key, data.footnote_args);

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      style={{
        width: '100%',
        height: '100%',
        padding: '12px 14px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'flex-start',
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
        {footnote && (
          <div style={{ fontSize: 9.5, color: 'var(--tx3)', textAlign: 'right' }}>
            {footnote}
          </div>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        {data.icon && <Icon name={data.icon} size={18} />}
        <span style={{
          fontSize: 28,
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

      <SparklineSvg series={data.series} stroke={stroke} rgb={rgb} />

      {data.breakdown && data.breakdown.length > 0 && (
        <div style={{ marginTop: 'auto' }}>
          <CardRow cards={data.breakdown} max={3} />
        </div>
      )}
    </motion.div>
  );
}

function SparklineSvg({
  series, stroke, rgb,
}: { series: number[]; stroke: string; rgb: string }) {
  if (!series || series.length < 2) {
    return <div style={{ flex: 1, minHeight: 24 }} />;
  }
  const min = Math.min(...series);
  const max = Math.max(...series);
  const padY = (max - min) * 0.08 || 1;
  const yMin = min - padY;
  const yMax = max + padY;

  const W = 100;
  const H = 36;
  const stepX = W / (series.length - 1);
  const points = series
    .map((v, i) => {
      const x = i * stepX;
      const t = (v - yMin) / (yMax - yMin || 1);
      const y = H - t * H;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(' ');
  const fillPoints = `0,${H} ${points} ${W},${H}`;
  const lastPoint = series[series.length - 1];
  const lastX = (series.length - 1) * stepX;
  const lastY = H - ((lastPoint - yMin) / (yMax - yMin || 1)) * H;

  // Gradient ID per tone — keeps multiple sparkline widgets from sharing
  // accidentally if more than one renders at the same time.
  const gradId = `sparkfill-${rgb.replace(/\D/g, '')}`;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      style={{ width: '100%', flex: 1, minHeight: 28, marginTop: 2, overflow: 'visible' }}
    >
      <defs>
        <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={`rgba(${rgb},.35)`} />
          <stop offset="100%" stopColor={`rgba(${rgb},0)`} />
        </linearGradient>
      </defs>
      <polygon points={fillPoints} fill={`url(#${gradId})`} />
      <polyline
        points={points}
        fill="none"
        stroke={stroke}
        strokeWidth={1.6}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      {/* Endpoint dot — visually anchors "now" to the latest data point. */}
      <circle cx={lastX} cy={lastY} r={1.6} fill={stroke} vectorEffect="non-scaling-stroke" />
    </svg>
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
