import React, { useEffect, useRef, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useStore } from '../store/useStore';

/* ── Notification Bell ── */
interface NotifyEntry { message: string; level: string; ts: string; channels: string[]; }
const LEVEL_COLOR: Record<string, string> = { info: '#60a5fa', warning: '#fbbf24', critical: '#f87171', error: '#f87171' };

function NotificationBell() {
  const [items, setItems] = useState<NotifyEntry[]>([]);
  const [open, setOpen] = useState(false);
  const [lastSeen, setLastSeen] = useState(0);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch('/api/ui/modules/notification-router/notify/history?limit=30');
        if (r.ok) setItems(await r.json());
      } catch { /* ignore */ }
    };
    poll();
    const id = setInterval(poll, 8000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const unread = items.filter((_, i) => i >= lastSeen).length;

  const handleOpen = () => {
    setOpen(o => !o);
    setLastSeen(items.length);
  };

  return (
    <div ref={panelRef} style={{ position: 'relative', flexShrink: 0 }}>
      <button
        onClick={handleOpen}
        style={{
          width: 32, height: 32, borderRadius: 8, border: 'none',
          background: open ? 'var(--sf3)' : 'transparent',
          color: unread > 0 ? 'var(--ac)' : 'var(--tx3)',
          cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
          position: 'relative', transition: 'all .15s',
        }}
      >
        <svg viewBox="0 0 16 16" fill="none" width="15" height="15">
          <path d="M8 1.5a5 5 0 0 0-5 5v2.5L1.5 11h13L13 9V6.5a5 5 0 0 0-5-5Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
          <path d="M6.5 13a1.5 1.5 0 0 0 3 0" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        </svg>
        {unread > 0 && (
          <div style={{
            position: 'absolute', top: 3, right: 3,
            width: 8, height: 8, borderRadius: '50%',
            background: 'var(--ac)', border: '1.5px solid var(--sf)',
          }} />
        )}
      </button>
      {open && (
        <div style={{
          position: 'absolute', top: '100%', right: 0, marginTop: 4,
          width: 300, maxHeight: 340, overflowY: 'auto',
          background: 'var(--sf)', border: '1px solid var(--b2)',
          borderRadius: 12, boxShadow: '0 8px 32px #0008', zIndex: 999,
        }}>
          <div style={{ padding: '10px 12px 8px', borderBottom: '1px solid var(--b)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--tx2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Notifications</span>
            <span style={{ fontSize: 10, color: 'var(--tx3)' }}>{items.length} total</span>
          </div>
          {items.length === 0 ? (
            <div style={{ padding: '20px 12px', textAlign: 'center', fontSize: 12, color: 'var(--tx3)' }}>No notifications</div>
          ) : [...items].reverse().map((n, i) => (
            <div key={i} style={{ padding: '8px 12px', borderBottom: '1px solid var(--b)', display: 'flex', flexDirection: 'column', gap: 3 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <div style={{ width: 6, height: 6, borderRadius: '50%', background: LEVEL_COLOR[n.level] || '#60a5fa', flexShrink: 0 }} />
                <span style={{ fontSize: 12, color: 'var(--tx)', flex: 1 }}>{n.message}</span>
              </div>
              <span style={{ fontSize: 10, color: 'var(--tx3)', marginLeft: 12 }}>{new Date(n.ts).toLocaleTimeString()}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Clock hook ── */
function useClock() {
  const [time, setTime] = useState('--:--');
  const [date, setDate] = useState('···');
  useEffect(() => {
    const update = () => {
      const n = new Date();
      const p = (x: number) => String(x).padStart(2, '0');
      setTime(`${p(n.getHours())}:${p(n.getMinutes())}`);
      const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
      const mons = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
      setDate(`${days[n.getDay()]} ${n.getDate()} ${mons[n.getMonth()]}`);
    };
    update();
    const id = setInterval(update, 15000);
    return () => clearInterval(id);
  }, []);
  return { time, date };
}

/* ── SVG icons ── */
const IcoHome = () => (
  <svg viewBox="0 0 18 18" fill="none" width="18" height="18">
    <path d="M2 8.5L9 2l7 6.5V16h-4v-4H6v4H2V8.5Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
  </svg>
);
const IcoModules = () => (
  <svg viewBox="0 0 18 18" fill="none" width="18" height="18">
    <rect x="2" y="2" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
    <rect x="10" y="2" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
    <rect x="2" y="10" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
    <rect x="10" y="10" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
  </svg>
);
const IcoSystem = () => (
  <svg viewBox="0 0 18 18" fill="none" width="18" height="18">
    <circle cx="9" cy="9" r="2.5" stroke="currentColor" strokeWidth="1.4" />
    <path d="M9 2v1.5M9 14.5V16M2 9h1.5M14.5 9H16M4.1 4.1l1.1 1.1M12.8 12.8l1.1 1.1M4.1 13.9l1.1-1.1M12.8 5.2l1.1-1.1" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
  </svg>
);
const IcoIntegrity = () => (
  <svg viewBox="0 0 18 18" fill="none" width="18" height="18">
    <path d="M9 2L3 5.5V10c0 3.5 2.5 6 6 7 3.5-1 6-3.5 6-7V5.5L9 2Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    <path d="M6.5 9.5l2 2 3.5-3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
const IcoMic = () => (
  <svg viewBox="0 0 13 13" fill="none" width="13" height="13">
    <rect x="4" y="1" width="5" height="7" rx="2.5" stroke="currentColor" strokeWidth="1.2" />
    <path d="M2 6c0 2.5 2 4.5 4.5 4.5S11 8.5 11 6M6.5 10.5V12.5M4.5 12.5h4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
  </svg>
);
const IcoSettings = () => (
  <svg viewBox="0 0 18 18" fill="none" width="18" height="18">
    <circle cx="9" cy="9" r="2" stroke="currentColor" strokeWidth="1.4" />
    <path d="M9 1.5v1.2M9 15.3V16.5M1.5 9h1.2M15.3 9h1.2M3.7 3.7l.85.85M13.45 13.45l.85.85M3.7 14.3l.85-.85M13.45 4.55l.85-.85" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    <circle cx="9" cy="9" r="3.5" stroke="currentColor" strokeWidth="1.4" />
  </svg>
);

const PAGES = [
  { id: 'home', path: '/', label: 'Home', Icon: IcoHome },
  { id: 'modules', path: '/modules', label: 'Modules', Icon: IcoModules },
  { id: 'system', path: '/system', label: 'System', Icon: IcoSystem },
  { id: 'integrity', path: '/integrity', label: 'Integrity', Icon: IcoIntegrity },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { time, date } = useClock();

  const stats = useStore((s) => s.stats);
  const health = useStore((s) => s.health);
  const fetchStats = useStore((s) => s.fetchStats);
  const fetchModules = useStore((s) => s.fetchModules);
  const voiceStatus = useStore((s) => s.voiceStatus);
  const user = useStore((s) => s.user);

  useEffect(() => {
    fetchStats();
    fetchModules();
    const id = setInterval(fetchStats, 30000);
    return () => clearInterval(id);
  }, [fetchStats, fetchModules]);

  const isActive = (path: string) =>
    path === '/' ? location.pathname === '/' : location.pathname.startsWith(path);

  const isOnline = health?.status === 'ok' || health?.status === 'ok';
  const intOk = (stats?.integrity ?? 'ok') === 'ok';

  const snav: React.CSSProperties = {
    width: 38, height: 38,
    borderRadius: 10,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    border: 'none',
    background: 'transparent',
    color: 'var(--tx3)',
    cursor: 'pointer',
    transition: 'all .15s',
    flexShrink: 0,
  };
  const snavActive: React.CSSProperties = {
    ...snav,
    background: 'var(--sf3)',
    color: 'var(--ac)',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: 'var(--bg)', overflow: 'hidden' }}>

      {/* ═══════════ TOPBAR ═══════════ */}
      <div style={{
        height: 'var(--topbar)',
        background: 'var(--sf)',
        borderBottom: '1px solid var(--b)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 8px',
        gap: 4,
        flexShrink: 0,
        zIndex: 10,
      }}>
        {/* Logo */}
        <div style={{
          width: 26, height: 26,
          background: 'var(--ac)',
          borderRadius: 7,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexShrink: 0, marginRight: 6,
        }}>
          <svg viewBox="0 0 14 14" fill="none" width="14" height="14">
            <path d="M7 1L2 4v5.5L7 13l5-3.5V4L7 1Z" stroke="white" strokeWidth="1.3" strokeLinejoin="round" />
            <circle cx="7" cy="7" r="1.8" fill="white" />
          </svg>
        </div>

        {/* Spacer */}
        <div style={{ flex: 1 }} />

        {/* Status cluster */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>

          {/* Online badge */}
          <div className={`net-status ${isOnline ? 'online' : 'offline'}`}>
            <div className="nsdot" />
            <span>{isOnline ? 'Online' : 'Offline'}</span>
          </div>

          {/* Wifi bars */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <div className="wifi-bars">
              <div className="wb active" style={{ height: 4 }} />
              <div className="wb active" style={{ height: 7 }} />
              <div className="wb active" style={{ height: 10 }} />
              <div className="wb inactive" style={{ height: 12 }} />
            </div>
          </div>

          {/* Voice activity indicator */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 4,
            color: voiceStatus === 'idle' ? 'var(--tx3)' : voiceStatus === 'listening' ? 'var(--gr)' : 'var(--ac)',
            transition: 'color .3s',
            flexShrink: 0,
          }}>
            {voiceStatus !== 'idle' && (
              <div style={{
                width: 5, height: 5, borderRadius: '50%',
                background: voiceStatus === 'listening' ? 'var(--gr)' : 'var(--ac)',
                boxShadow: `0 0 5px ${voiceStatus === 'listening' ? 'var(--gr)' : 'var(--ac)'}`,
              }} className="live-dot" />
            )}
            <IcoMic />
          </div>

          {/* Separator */}
          <div style={{ width: 1, height: 18, background: 'var(--b2)', margin: '0 2px', flexShrink: 0 }} />

          {/* Notification Bell */}
          <NotificationBell />

          {/* Clock */}
          <div style={{ textAlign: 'center', flexShrink: 0 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 500, color: 'var(--tx)', lineHeight: 1 }}>
              {time}
            </div>
            <div style={{ fontSize: 9, color: 'var(--tx3)', marginTop: 1 }}>
              {date}
            </div>
          </div>
        </div>
      </div>

      {/* ═══════════ BODY ═══════════ */}
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: 'var(--sidebar) 1fr', overflow: 'hidden' }}>

        {/* ── SIDEBAR ── */}
        <nav style={{
          background: 'var(--sf)',
          borderRight: '1px solid var(--b)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: '8px 0',
          gap: 4,
        }}>
          {/* Home, Modules */}
          {PAGES.slice(0, 2).map(({ id, path, label, Icon }) => (
            <button
              key={id}
              title={label}
              style={isActive(path) ? snavActive : snav}
              onClick={() => navigate(path)}
            >
              <Icon />
            </button>
          ))}

          {/* Separator */}
          <div style={{ width: 28, height: 1, background: 'var(--b)', margin: '4px 0', flexShrink: 0 }} />

          {/* System, Integrity */}
          {PAGES.slice(2).map(({ id, path, label, Icon }) => (
            <button
              key={id}
              title={label}
              style={isActive(path) ? snavActive : snav}
              onClick={() => navigate(path)}
            >
              <Icon />
            </button>
          ))}

          <div style={{ flex: 1 }} />

          {/* Separator */}
          <div style={{ width: 28, height: 1, background: 'var(--b)', margin: '4px 0', flexShrink: 0 }} />

          {/* User role avatar */}
          {user && (
            <button
              title={`${user.name} · ${user.role}`}
              onClick={() => navigate('/settings/users')}
              style={{
                width: 30, height: 30, borderRadius: '50%',
                border: `2px solid ${user.role === 'owner' ? '#7c3aed'
                  : user.role === 'admin' ? '#3b82f6'
                    : user.role === 'user' ? '#10b981'
                      : '#52525b'
                  }`,
                background: 'var(--sf3)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontWeight: 700,
                color: 'var(--tx)',
                cursor: 'pointer',
                flexShrink: 0,
              }}
            >
              {user.name.slice(0, 1).toUpperCase()}
            </button>
          )}

          {/* Settings */}
          <button
            title="Settings"
            style={isActive('/settings') ? snavActive : snav}
            onClick={() => navigate('/settings')}
          >
            <IcoSettings />
          </button>

          {/* Integrity dot */}
          <div style={{
            width: 6, height: 6, borderRadius: '50%',
            background: intOk ? 'var(--gr)' : 'var(--rd)',
            marginBottom: 8, flexShrink: 0,
            boxShadow: `0 0 5px ${intOk ? 'var(--gr)' : 'var(--rd)'}`,
          }} className="live-dot" />
        </nav>

        {/* ── PAGE CONTENT ── */}
        <main style={{ position: 'relative', overflow: 'hidden' }}>
          {children}
        </main>
      </div>

    </div>
  );
}
