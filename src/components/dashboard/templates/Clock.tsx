import { useEffect, useState } from 'react';
import { motion } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { useWidgetData } from '../../../hooks/useWidgetData';
import { GenericSkeleton } from './Skeleton';
import type { TemplateProps } from './registry';

export interface ClockPayload {
  now_iso: string;
  timezone: string;
  format: '12h' | '24h';
  style: 'digital' | 'analog';
  next_alarm: { hour: number; minute: number; label?: string | null; next_run?: string | null } | null;
  active_timers_count: number;
  pending_reminders_count: number;
  ringing_alarms_count: number;
}

interface TzParts {
  hour: number;
  minute: number;
  second: number;
  weekday: string;
  date: string;
}

function partsInTz(d: Date, tz: string): TzParts {
  try {
    const fmt = new Intl.DateTimeFormat('en-GB', {
      timeZone: tz,
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      weekday: 'short',
      day: '2-digit',
      month: 'short',
    });
    const parts = fmt.formatToParts(d);
    const get = (k: string) => parts.find((p) => p.type === k)?.value ?? '';
    return {
      hour: parseInt(get('hour'), 10) || 0,
      minute: parseInt(get('minute'), 10) || 0,
      second: parseInt(get('second'), 10) || 0,
      weekday: get('weekday'),
      date: `${get('day')} ${get('month')}`,
    };
  } catch {
    return {
      hour: d.getHours(),
      minute: d.getMinutes(),
      second: d.getSeconds(),
      weekday: '',
      date: '',
    };
  }
}

function formatHM(h: number, m: number, fmt: '12h' | '24h'): string {
  if (fmt === '12h') {
    const hh = ((h + 11) % 12) + 1;
    return `${hh}:${m.toString().padStart(2, '0')}`;
  }
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`;
}

function ampm(h: number): string {
  return h < 12 ? 'AM' : 'PM';
}

export default function ClockTemplate({ mod }: TemplateProps) {
  const { t: tr } = useTranslation();
  const widget = mod.ui?.widget;
  const { data, loading, error, refetch } = useWidgetData<ClockPayload>({
    module: mod.name,
    key: 'state',
    events: widget?.refresh?.events,
    pollIntervalS: widget?.refresh?.poll_interval_s,
  });

  const [tick, setTick] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const now = new Date(tick);

  if (loading && !data) return <GenericSkeleton />;
  if (error && !data) return <ErrorBlock onRetry={refetch} message={error} />;
  if (!data) return <GenericSkeleton />;

  const tz = data.timezone;
  const parts = partsInTz(now, tz);

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
        gap: 8,
      }}
    >
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'flex-start',
        fontSize: 9.5,
        fontWeight: 600,
        color: 'var(--tx3)',
        textTransform: 'uppercase',
        letterSpacing: '.08em',
      }}>
        <span>{parts.weekday} · {parts.date}</span>
        <span>{tz.replace(/_/g, ' ')}</span>
      </div>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 0 }}>
        {data.style === 'analog'
          ? <AnalogClock h={parts.hour} m={parts.minute} s={parts.second} />
          : <DigitalClock h={parts.hour} m={parts.minute} s={parts.second} format={data.format} />}
      </div>

      <Chips data={data} tr={tr} />
    </motion.div>
  );
}

function DigitalClock({ h, m, s, format }: { h: number; m: number; s: number; format: '12h' | '24h' }) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'baseline',
      gap: 4,
      fontVariantNumeric: 'tabular-nums',
      lineHeight: 1,
      letterSpacing: '-.02em',
      color: 'var(--tx)',
    }}>
      <span style={{ fontSize: 52, fontWeight: 300 }}>{formatHM(h, m, format)}</span>
      <span style={{ fontSize: 18, color: 'var(--tx3)', fontWeight: 400 }}>
        :{s.toString().padStart(2, '0')}
      </span>
      {format === '12h' && (
        <span style={{ fontSize: 11, color: 'var(--tx3)', fontWeight: 600, marginLeft: 4 }}>
          {ampm(h)}
        </span>
      )}
    </div>
  );
}

function AnalogClock({ h, m, s }: { h: number; m: number; s: number }) {
  const hAng = ((h % 12) + m / 60) * 30;
  const mAng = (m + s / 60) * 6;
  const sAng = s * 6;
  const size = 110;
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 4;
  return (
    <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} aria-hidden>
      <circle cx={cx} cy={cy} r={r} fill="var(--sf2)" stroke="var(--b)" strokeWidth={1.5} />
      {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map((i) => {
        const ang = (i * 30 - 90) * (Math.PI / 180);
        const inner = i % 3 === 0 ? r - 8 : r - 4;
        const outer = r - 1;
        const x1 = cx + Math.cos(ang) * inner;
        const y1 = cy + Math.sin(ang) * inner;
        const x2 = cx + Math.cos(ang) * outer;
        const y2 = cy + Math.sin(ang) * outer;
        return (
          <line
            key={i}
            x1={x1} y1={y1} x2={x2} y2={y2}
            stroke={i % 3 === 0 ? 'var(--tx2)' : 'var(--tx3)'}
            strokeWidth={i % 3 === 0 ? 2 : 1}
            strokeLinecap="round"
          />
        );
      })}
      <Hand cx={cx} cy={cy} angle={hAng} length={r * 0.5} width={3.2} color="var(--tx)" />
      <Hand cx={cx} cy={cy} angle={mAng} length={r * 0.75} width={2.4} color="var(--tx)" />
      <Hand cx={cx} cy={cy} angle={sAng} length={r * 0.85} width={1} color="var(--rd)" />
      <circle cx={cx} cy={cy} r={2.6} fill="var(--tx)" />
    </svg>
  );
}

function Hand({ cx, cy, angle, length, width, color }: {
  cx: number; cy: number; angle: number; length: number; width: number; color: string;
}) {
  const rad = (angle - 90) * (Math.PI / 180);
  const x2 = cx + Math.cos(rad) * length;
  const y2 = cy + Math.sin(rad) * length;
  return (
    <line
      x1={cx} y1={cy} x2={x2} y2={y2}
      stroke={color} strokeWidth={width} strokeLinecap="round"
    />
  );
}

function Chips({
  data, tr,
}: {
  data: ClockPayload;
  tr: (key: string, opts?: Record<string, unknown>) => string;
}) {
  const chips: { key: string; text: string; tone: 'alert' | 'info' | 'neutral'; title?: string }[] = [];
  if (data.ringing_alarms_count > 0) {
    const ringingText = data.ringing_alarms_count > 1
      ? tr('widgets.clock.chipRingingMany', {
          count: data.ringing_alarms_count,
          defaultValue: `🔔 Ringing (${data.ringing_alarms_count})`,
        })
      : tr('widgets.clock.chipRinging', { defaultValue: '🔔 Ringing' });
    chips.push({ key: 'ringing', text: ringingText, tone: 'alert' });
  }
  if (data.next_alarm) {
    const at = formatHM(data.next_alarm.hour, data.next_alarm.minute, data.format);
    chips.push({
      key: 'alarm',
      text: `⏰ ${at}`,
      tone: 'info',
      title: data.next_alarm.label || tr('widgets.clock.nextAlarm', { defaultValue: 'Next alarm' }),
    });
  }
  if (data.active_timers_count > 0) {
    chips.push({ key: 'timer', text: `⏱ ${data.active_timers_count}`, tone: 'info' });
  }
  if (data.pending_reminders_count > 0) {
    chips.push({ key: 'rem', text: `📋 ${data.pending_reminders_count}`, tone: 'neutral' });
  }
  if (chips.length === 0) return <div style={{ height: 16 }} />;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'center' }}>
      {chips.map((c) => (
        <span
          key={c.key}
          title={c.title}
          style={{
            fontSize: 10,
            fontWeight: 600,
            padding: '3px 8px',
            borderRadius: 999,
            color: c.tone === 'alert' ? 'var(--rd)' : c.tone === 'info' ? 'var(--ac)' : 'var(--tx2)',
            background: c.tone === 'alert'
              ? 'color-mix(in srgb, var(--rd) 14%, transparent)'
              : 'color-mix(in srgb, var(--sf2) 80%, transparent)',
            border: '1px solid var(--b)',
            fontVariantNumeric: 'tabular-nums',
            whiteSpace: 'nowrap',
          }}
        >
          {c.text}
        </span>
      ))}
    </div>
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
