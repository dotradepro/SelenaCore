import { useState } from 'react';
import { motion } from 'motion/react';
import { useWidgetData } from '../../../hooks/useWidgetData';
import { useStore } from '../../../store/useStore';
import Icon from './Icon';
import type { TemplateProps } from './registry';

/** Payload — see docs/dashboard-recraft.md §3.3 (Phase 6, ``media``). */
export interface MediaPayload {
  state: 'play' | 'pause' | 'stop' | 'idle';
  track: {
    title: string;
    artist?: string | null;
    album?: string | null;
    cover_url?: string | null;
    source_type?: string | null;
    duration_sec?: number | null;
  } | null;
  volume: number;
  position_sec?: number | null;
  shuffle?: boolean;
}

/** 4 transport buttons rendered as icon-only round controls. The PLAY
 *  button is the centre-piece — bigger and tone-coloured when active.
 *  Heavy emphasis on tap targets so the kiosk works with fingers. */
const TRANSPORT: { id: string; emoji: string; primary?: boolean }[] = [
  { id: 'previous', emoji: '⏮' },
  { id: 'play', emoji: '▶', primary: true },
  { id: 'pause', emoji: '⏸' },
  { id: 'next', emoji: '⏭' },
];

export default function MediaTemplate({ mod }: TemplateProps) {
  const widget = mod.ui?.widget;
  const { data, loading, error, refetch } = useWidgetData<MediaPayload>({
    module: mod.name,
    key: 'state',
    events: widget?.refresh?.events,
    pollIntervalS: widget?.refresh?.poll_interval_s,
  });

  const [pendingTransport, setPendingTransport] = useState<string | null>(null);
  const [pendingVolume, setPendingVolume] = useState(false);

  async function transport(id: string) {
    if (pendingTransport) return;
    setPendingTransport(id);
    try {
      const r = await fetch(`/api/v1/modules/${mod.name}/action/set_mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refetch();
    } catch {
      useStore.getState().showToast('Transport failed', 'error');
    } finally {
      setPendingTransport(null);
    }
  }

  async function setVolume(value: number) {
    if (pendingVolume) return;
    setPendingVolume(true);
    try {
      const r = await fetch(`/api/v1/modules/${mod.name}/action/step`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: 'volume', value }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refetch();
    } catch {
      useStore.getState().showToast('Volume failed', 'error');
    } finally {
      setPendingVolume(false);
    }
  }

  if (loading && !data) return <MediaSkeleton />;
  if (error && !data) return <ErrorBlock onRetry={refetch} message={error} />;
  if (!data) return <MediaSkeleton />;

  const track = data.track;
  const isPlaying = data.state === 'play';
  const hasTrack = !!track;

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
      {/* Cover + track meta */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', minHeight: 72 }}>
        <CoverArt track={track} playing={isPlaying} />
        <div style={{ minWidth: 0, flex: 1, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <div style={{
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--tx)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            lineHeight: 1.25,
          }}>
            {track?.title ?? 'Nothing playing'}
          </div>
          {track?.artist && (
            <div style={{
              fontSize: 11,
              color: 'var(--tx2)',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}>
              {track.artist}
            </div>
          )}
          {track?.source_type && (
            <div style={{
              alignSelf: 'flex-start',
              marginTop: 4,
              fontSize: 8.5,
              fontWeight: 600,
              color: 'var(--tx3)',
              textTransform: 'uppercase',
              letterSpacing: '.08em',
              padding: '2px 6px',
              borderRadius: 4,
              background: 'var(--sf2)',
              border: '1px solid var(--b)',
            }}>
              {track.source_type}
            </div>
          )}
        </div>
      </div>

      {/* Transport row */}
      <div style={{ display: 'flex', gap: 6, justifyContent: 'center', alignItems: 'center' }}>
        {TRANSPORT.map((t) => {
          const active = (t.id === 'play' && isPlaying);
          const busy = pendingTransport === t.id;
          const big = !!t.primary;
          const dim = !hasTrack && t.id !== 'play';
          return (
            <motion.button
              key={t.id}
              onClick={() => transport(t.id)}
              disabled={busy || dim}
              whileTap={{ scale: 0.92 }}
              transition={{ duration: 0.15, ease: [0.5, 1.4, 0.5, 1] }}
              style={{
                width: big ? 44 : 34,
                height: big ? 44 : 34,
                borderRadius: '50%',
                fontSize: big ? 16 : 13,
                fontFamily: '"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif',
                cursor: busy ? 'wait' : dim ? 'default' : 'pointer',
                opacity: busy ? 0.6 : dim ? 0.35 : 1,
                background: active ? 'var(--ac)' : 'var(--sf)',
                color: active ? 'var(--on-accent)' : 'var(--tx)',
                border: `1px solid ${active ? 'var(--ac)' : 'var(--b)'}`,
                boxShadow: active ? 'var(--widget-glow-on)' : 'none',
                transition: 'background .15s, border-color .15s',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                lineHeight: 1,
              }}
            >
              {t.emoji}
            </motion.button>
          );
        })}
      </div>

      {/* Volume */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 'auto' }}>
        <Icon name="volume-2" size={14} />
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={data.volume}
          disabled={pendingVolume}
          onChange={(e) => setVolume(parseInt(e.target.value, 10))}
          style={{
            flex: 1,
            accentColor: 'var(--ac)',
            cursor: pendingVolume ? 'wait' : 'pointer',
          }}
        />
        <span style={{
          fontSize: 10,
          color: 'var(--tx3)',
          minWidth: 30,
          textAlign: 'right',
          fontVariantNumeric: 'tabular-nums',
        }}>
          {data.volume}%
        </span>
      </div>
    </motion.div>
  );
}

function CoverArt({ track, playing }: { track: MediaPayload['track']; playing: boolean }) {
  const cover = track?.cover_url;
  if (cover) {
    return (
      <motion.img
        src={cover}
        alt={track?.title ?? 'cover'}
        animate={playing ? { rotate: [0, 0.5, -0.5, 0] } : { rotate: 0 }}
        transition={{ duration: 12, repeat: playing ? Infinity : 0, ease: 'linear' }}
        style={{
          width: 64, height: 64, borderRadius: 10,
          objectFit: 'cover', flexShrink: 0,
          border: '1px solid var(--b)',
          boxShadow: playing ? '0 6px 20px rgba(90,150,255,.20)' : 'var(--shadow)',
        }}
        onError={(e: any) => { e.currentTarget.style.display = 'none'; }}
      />
    );
  }
  return (
    <div style={{
      width: 64, height: 64, borderRadius: 10,
      background: 'linear-gradient(135deg, var(--sf2), var(--sf3))',
      border: '1px solid var(--b)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      flexShrink: 0,
      fontSize: 26,
      fontFamily: '"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif',
    }}>
      🎵
    </div>
  );
}

export function MediaSkeleton() {
  return (
    <div style={{ width: '100%', height: '100%', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', gap: 12 }}>
        <div style={skel(64, 64, 10)} />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={skel(13, '70%')} />
          <div style={skel(11, '50%')} />
        </div>
      </div>
      <div style={{ display: 'flex', gap: 6, justifyContent: 'center' }}>
        <div style={skel(34, 34, 17)} />
        <div style={skel(44, 44, 22)} />
        <div style={skel(34, 34, 17)} />
        <div style={skel(34, 34, 17)} />
      </div>
      <div style={skel(8, '100%')} />
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
