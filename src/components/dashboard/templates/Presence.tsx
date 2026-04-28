import { motion } from 'motion/react';
import { useWidgetData } from '../../../hooks/useWidgetData';
import Icon from './Icon';
import Pill from './blocks/Pill';
import type { TemplateProps } from './registry';

/** Payload shape — see docs/dashboard-recraft.md §3.3 (Phase 6, ``presence``). */
export interface PresenceUser {
  id: string;
  name: string;
  state: 'home' | 'away' | 'unknown';
  last_seen?: string | null;
  icon?: string | null;
  badge?: string | null;
}

export interface PresencePayload {
  summary: { tone: 'ok' | 'info' | 'warn' | 'neutral'; text: string; icon?: string };
  users: PresenceUser[];
  empty_text?: string;
}

const STATE_DOT: Record<PresenceUser['state'], string> = {
  home: 'var(--gr)',
  away: 'var(--am)',
  unknown: 'var(--tx3)',
};

const STATE_LABEL: Record<PresenceUser['state'], string> = {
  home: 'Home',
  away: 'Away',
  unknown: '—',
};

export default function PresenceTemplate({ mod }: TemplateProps) {
  const widget = mod.ui?.widget;
  const { data, loading, error, refetch } = useWidgetData<PresencePayload>({
    module: mod.name,
    key: 'state',
    events: widget?.refresh?.events,
    pollIntervalS: widget?.refresh?.poll_interval_s,
  });

  if (loading && !data) return <PresenceSkeleton />;
  if (error && !data) return <ErrorBlock onRetry={refetch} message={error} />;
  if (!data) return <PresenceSkeleton />;

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
        gap: 10,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{
          fontSize: 9.5, fontWeight: 600, color: 'var(--tx3)',
          textTransform: 'uppercase', letterSpacing: '.08em',
        }}>
          Presence
        </div>
        <Pill tone={data.summary.tone} text={data.summary.text} icon={data.summary.icon} />
      </div>

      {data.users.length === 0 ? (
        <div style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
          color: 'var(--tx3)',
          fontSize: 11,
        }}>
          <span style={{ fontSize: 28 }}>👤</span>
          {data.empty_text ?? 'No users yet'}
        </div>
      ) : (
        <div style={{
          flex: 1,
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
          gap: 8,
          overflowY: 'auto',
          alignContent: 'start',
        }}>
          {data.users.map((user) => (
            <UserCard key={user.id} user={user} />
          ))}
        </div>
      )}
    </motion.div>
  );
}

function UserCard({ user }: { user: PresenceUser }) {
  const dot = STATE_DOT[user.state];
  const initial = (user.name || '?').trim().charAt(0).toUpperCase() || '?';

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      padding: '8px 10px',
      borderRadius: 12,
      background: 'var(--sf)',
      border: '1px solid var(--b)',
      transition: 'border-color .15s, background .15s',
    }}>
      <Avatar user={user} initial={initial} dot={dot} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{
          fontSize: 11.5,
          fontWeight: 600,
          color: 'var(--tx)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          lineHeight: 1.2,
        }}>
          {user.name}
        </div>
        <div style={{
          fontSize: 9.5,
          color: dot,
          marginTop: 2,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}>
          {user.last_seen ?? STATE_LABEL[user.state]}
        </div>
      </div>
      {user.badge && (
        <span style={{
          fontSize: 8,
          color: 'var(--tx3)',
          background: 'var(--sf2)',
          borderRadius: 4,
          padding: '1px 4px',
          flexShrink: 0,
          textTransform: 'uppercase',
          letterSpacing: '.04em',
          fontWeight: 600,
        }}>
          {user.badge}
        </span>
      )}
    </div>
  );
}

function Avatar({
  user, initial, dot,
}: { user: PresenceUser; initial: string; dot: string }) {
  return (
    <div style={{ position: 'relative', flexShrink: 0 }}>
      <div style={{
        width: 30, height: 30, borderRadius: '50%',
        background: 'linear-gradient(135deg, var(--sf2), var(--sf3))',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: '1px solid var(--b)',
      }}>
        {user.icon ? (
          <Icon name={user.icon} size={16} fallback={initial} />
        ) : (
          <span style={{
            fontSize: 12,
            fontWeight: 600,
            color: 'var(--tx)',
          }}>
            {initial}
          </span>
        )}
      </div>
      {/* Status dot — small overlay bottom-right of avatar. */}
      <span
        aria-hidden
        style={{
          position: 'absolute',
          right: -1, bottom: -1,
          width: 9, height: 9, borderRadius: '50%',
          background: dot,
          border: '2px solid var(--sf)',
        }}
      />
    </div>
  );
}

export function PresenceSkeleton() {
  return (
    <div style={{ width: '100%', height: '100%', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={skel(9, '40%')} />
        <div style={skel(20, 90, 999)} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 8 }}>
        {[0, 1, 2, 3].map((i) => <div key={i} style={skel(40, '100%', 12)} />)}
      </div>
    </div>
  );
}

function skel(height: number, width: string | number, radius = 6): React.CSSProperties {
  return {
    height,
    width,
    background: 'var(--skeleton-bg)',
    backgroundSize: '200% 100%',
    borderRadius: radius,
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
