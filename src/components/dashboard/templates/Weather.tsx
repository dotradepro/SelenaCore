import { motion } from 'motion/react';
import { useWidgetData } from '../../../hooks/useWidgetData';
import Icon from './Icon';
import type { TemplateProps } from './registry';

/** Payload shape — see docs/dashboard-recraft.md §3.3 (Phase 6).
 *  ``icon`` is a lucide-style name resolved by ``Icon`` to an emoji
 *  glyph, ``emoji`` is the module-supplied fallback (the WMO mapping
 *  weather-service already had). */
export interface WeatherPayload {
  location?: string;
  current: {
    icon?: string | null;
    emoji?: string | null;
    temperature: number;
    unit?: '°C' | '°F';
    condition: string;
    feels_like?: number | null;
  };
  pills?: { icon?: string; value: string; label?: string }[];
  forecast?: {
    day: string;
    icon?: string;
    high: number;
    low: number;
    unit?: '°C' | '°F';
  }[];
}

/** Soft gradient backgrounds keyed off the lucide icon name — gives the
 *  hero region a time-of-day / condition cue inspired by Apple Weather. */
const HERO_GRADIENT: Record<string, string> = {
  'sun':         'radial-gradient(ellipse at 30% 20%, rgba(255,196,80,.18), transparent 70%)',
  'cloud':       'radial-gradient(ellipse at 30% 20%, rgba(160,170,200,.14), transparent 70%)',
  'cloud-rain':  'radial-gradient(ellipse at 30% 20%, rgba(90,150,255,.18), transparent 70%)',
  'cloud-snow':  'radial-gradient(ellipse at 30% 20%, rgba(200,220,255,.18), transparent 70%)',
  'zap':         'radial-gradient(ellipse at 30% 20%, rgba(155,110,244,.20), transparent 70%)',
};

export default function WeatherTemplate({ mod }: TemplateProps) {
  const widget = mod.ui?.widget;
  const { data, loading, error, refetch } = useWidgetData<WeatherPayload>({
    module: mod.name,
    key: 'state',
    events: widget?.refresh?.events,
    pollIntervalS: widget?.refresh?.poll_interval_s,
  });

  if (loading && !data) return <WeatherSkeleton />;
  if (error && !data) return <ErrorBlock onRetry={refetch} message={error} />;
  if (!data) return <WeatherSkeleton />;

  const unit = data.current.unit ?? '°C';
  const heroBg = data.current.icon ? HERO_GRADIENT[data.current.icon] : undefined;

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      style={{
        width: '100%',
        height: '100%',
        padding: '14px 16px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        background: heroBg,
      }}
    >
      {data.location && (
        <div style={{
          fontSize: 9.5, fontWeight: 600, color: 'var(--tx3)',
          textTransform: 'uppercase', letterSpacing: '.08em',
        }}>
          {data.location}
        </div>
      )}

      {/* Hero — big emoji on the left, big temp + condition on the right */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <Icon
          name={data.current.icon}
          fallback={data.current.emoji ?? '☁️'}
          size={48}
        />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
          <div style={{
            fontSize: 38,
            fontWeight: 300,
            color: 'var(--tx)',
            lineHeight: 1,
            letterSpacing: '-.02em',
          }}>
            {Math.round(data.current.temperature)}{unit}
          </div>
          <div style={{
            fontSize: 12,
            color: 'var(--tx2)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}>
            {data.current.condition}
            {data.current.feels_like !== undefined && data.current.feels_like !== null && (
              <span style={{ color: 'var(--tx3)' }}>
                {'  ·  '}feels {Math.round(data.current.feels_like)}°
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Telemetry pills — subtle row of icon + value pairs */}
      {data.pills && data.pills.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
          {data.pills.slice(0, 4).map((p, i) => (
            <div key={i} style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              fontSize: 11, color: 'var(--tx2)', fontVariantNumeric: 'tabular-nums',
            }}>
              <Icon name={p.icon} size={13} />
              <span>{p.value}</span>
              {p.label && <span style={{ color: 'var(--tx3)' }}>{p.label}</span>}
            </div>
          ))}
        </div>
      )}

      {data.forecast && data.forecast.length > 0 && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: `repeat(${Math.min(3, data.forecast.length)}, minmax(0, 1fr))`,
          gap: 6,
          marginTop: 'auto',
        }}>
          {data.forecast.slice(0, 3).map((f, i) => (
            <ForecastCard key={i} f={f} unitFallback={unit} />
          ))}
        </div>
      )}
    </motion.div>
  );
}

function ForecastCard({
  f, unitFallback,
}: { f: NonNullable<WeatherPayload['forecast']>[0]; unitFallback: '°C' | '°F' }) {
  const u = f.unit ?? unitFallback;
  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 2,
      padding: '8px 4px 6px',
      borderRadius: 10,
      background: 'color-mix(in srgb, var(--sf2) 80%, transparent)',
      border: '1px solid var(--b)',
    }}>
      <div style={{
        fontSize: 9, fontWeight: 600, color: 'var(--tx3)',
        textTransform: 'uppercase', letterSpacing: '.06em',
      }}>
        {f.day}
      </div>
      <Icon name={f.icon} size={22} />
      <div style={{ fontSize: 11, color: 'var(--tx)', fontVariantNumeric: 'tabular-nums', letterSpacing: '.01em' }}>
        <span style={{ color: 'var(--am)', fontWeight: 600 }}>{Math.round(f.high)}°</span>
        <span style={{ color: 'var(--tx3)', margin: '0 3px' }}>·</span>
        <span style={{ color: 'var(--ac)' }}>{Math.round(f.low)}°{u === '°F' ? 'F' : ''}</span>
      </div>
    </div>
  );
}

export function WeatherSkeleton() {
  return (
    <div style={{ width: '100%', height: '100%', padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={skel(9, '40%')} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <div style={skel(48, 48)} />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={skel(28, '50%')} />
          <div style={skel(11, '70%')} />
        </div>
      </div>
      <div style={{ display: 'flex', gap: 10 }}>
        <div style={skel(11, 60)} />
        <div style={skel(11, 60)} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6, marginTop: 'auto' }}>
        <div style={skel(60, '100%')} />
        <div style={skel(60, '100%')} />
        <div style={skel(60, '100%')} />
      </div>
    </div>
  );
}

function skel(height: number, width: string | number): React.CSSProperties {
  return {
    height,
    width,
    background: 'var(--skeleton-bg)',
    backgroundSize: '200% 100%',
    borderRadius: 6,
    animation: 'skeletonShimmer 1.4s linear infinite',
  };
}

function ErrorBlock({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div style={{
      width: '100%', height: '100%', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', gap: 6,
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
