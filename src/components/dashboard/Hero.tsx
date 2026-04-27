import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../../store/useStore';
import { useTimeOfDay } from '../../hooks/useTimeOfDay';

/** Status-pill state derived from health + module list + integrity. See
 *  docs/dashboard-recraft.md §2.2.1 for the colour/text mapping. */
type PillState = 'ok' | 'warning' | 'degraded' | 'safe';

interface PillView {
  state: PillState;
  text: string;
  href: string | null;
}

function derivePill(
  modules: { status: string; name: string }[],
  health: { mode?: string; integrity?: string } | null,
  t: (k: string, v?: Record<string, unknown>) => string,
): PillView {
  if (health?.mode === 'safe_mode') {
    return { state: 'safe', text: t('dashboardV2.pill.safe'), href: '/settings/system-info' };
  }
  if (health?.integrity && health.integrity !== 'ok') {
    return { state: 'warning', text: t('dashboardV2.pill.warning'), href: '/settings/system-info' };
  }
  const errored = modules.find((m) => m.status === 'ERROR');
  if (errored) {
    return {
      state: 'degraded',
      text: t('dashboardV2.pill.degraded', { name: errored.name }),
      href: '/settings/system-info',
    };
  }
  const active = modules.filter((m) => m.status === 'RUNNING').length;
  return { state: 'ok', text: t('dashboardV2.pill.ok', { count: active }), href: null };
}

function greetingKey(tod: ReturnType<typeof useTimeOfDay>): string {
  if (tod === 'morning') return 'dashboardV2.greeting.morning';
  if (tod === 'evening' || tod === 'night') return 'dashboardV2.greeting.evening';
  return 'dashboardV2.greeting.day';
}

/** Returns the user's first name (segment before the first whitespace), or
 *  an empty string for guests. We strip the part to keep the greeting short
 *  on small screens. */
function firstName(name: string | undefined | null): string {
  if (!name) return '';
  const trimmed = name.trim();
  if (!trimmed) return '';
  const ws = trimmed.indexOf(' ');
  return ws > 0 ? trimmed.slice(0, ws) : trimmed;
}

interface OutdoorWeather {
  temperature: number;
  condition: string;
  unit?: 'C' | 'F';
}

/** Probe the optional weather module. Returns null if the module is missing
 *  or the call fails — Hero hides the right-side block silently. */
async function fetchWeather(): Promise<OutdoorWeather | null> {
  try {
    const r = await fetch('/api/ui/modules/weather-service/weather/current');
    if (!r.ok) return null;
    const raw = await r.json();
    if (typeof raw?.temperature !== 'number') return null;
    const units = raw?.units ?? 'celsius';
    return {
      temperature: raw.temperature,
      condition: raw.condition_emoji
        ? `${raw.condition_emoji} ${raw.condition || ''}`.trim()
        : (raw.condition || '—'),
      unit: units === 'fahrenheit' ? 'F' : 'C',
    };
  } catch {
    return null;
  }
}

export default function Hero() {
  const { t, i18n } = useTranslation();
  const tod = useTimeOfDay();
  const user = useStore((s) => s.user);
  const modules = useStore((s) => s.modules);
  const health = useStore((s) => s.health);

  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  const [weather, setWeather] = useState<OutdoorWeather | null>(null);
  useEffect(() => {
    let alive = true;
    fetchWeather().then((w) => { if (alive) setWeather(w); });
    return () => { alive = false; };
  }, []);

  const dateText = now.toLocaleDateString(i18n.language || 'en', {
    weekday: 'long', month: 'long', day: 'numeric',
  });
  const timeText = now.toLocaleTimeString(i18n.language || 'en', {
    hour: '2-digit', minute: '2-digit',
  });

  const greeting = t(greetingKey(tod), { name: firstName(user?.name) || t('dashboardV2.greeting.fallbackName') });
  const pill = derivePill(modules, health, (k, v) => t(k, v) as string);

  const pillToneVar: Record<PillState, string> = {
    ok: 'var(--gr)',
    warning: 'var(--am)',
    degraded: 'var(--am)',
    safe: 'var(--rd)',
  };

  return (
    <div
      className="dashboard-hero"
      style={{
        position: 'relative',
        padding: '14px 18px 12px',
        display: 'flex',
        alignItems: 'flex-end',
        justifyContent: 'space-between',
        gap: 12,
        background: 'radial-gradient(ellipse at 20% 0%, var(--hero-tint) 0%, transparent 60%)',
        borderRadius: 12,
        marginBottom: 10,
      }}
    >
      <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ fontSize: 11, color: 'var(--tx3)', textTransform: 'capitalize' }}>
          {dateText} · {timeText}
        </div>
        <div style={{ fontSize: 22, fontWeight: 600, color: 'var(--tx)', lineHeight: 1.15 }}>
          {greeting}
        </div>
        <Pill view={pill} toneVar={pillToneVar[pill.state]} />
      </div>

      {weather && (
        <div style={{ flexShrink: 0, textAlign: 'right' }}>
          <div style={{ fontSize: 22, fontWeight: 600, color: 'var(--tx)' }}>
            {Math.round(weather.temperature)}°{weather.unit || 'C'}
          </div>
          <div style={{ fontSize: 11, color: 'var(--tx3)' }}>{weather.condition}</div>
        </div>
      )}
    </div>
  );
}

function Pill({ view, toneVar }: { view: PillView; toneVar: string }) {
  const inner = (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '3px 9px',
        borderRadius: 999,
        fontSize: 10.5,
        fontWeight: 500,
        color: toneVar,
        background: 'color-mix(in srgb, var(--sf2) 70%, transparent)',
        border: '1px solid var(--b)',
      }}
    >
      <span
        aria-hidden
        style={{ width: 6, height: 6, borderRadius: '50%', background: toneVar, display: 'inline-block' }}
      />
      {view.text}
    </span>
  );
  if (view.href) {
    return (
      <a href={view.href} style={{ textDecoration: 'none', alignSelf: 'flex-start' }}>
        {inner}
      </a>
    );
  }
  return <div style={{ alignSelf: 'flex-start' }}>{inner}</div>;
}
