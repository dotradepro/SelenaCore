import { useMemo, useRef, useState } from 'react';
import type {
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
} from 'react';
import { motion } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { useWidgetData } from '../../../hooks/useWidgetData';
import { useStore } from '../../../store/useStore';
import { ToggleListSkeleton } from './Skeleton';
import Icon from './Icon';
import { resolveLabel } from './i18n';
import { ALL_ROOM, SYSTEM_ROOM } from '../RoomTabs';
import type { TemplateProps } from './registry';

export interface ToggleItem {
  id: string;
  name: string;
  state: 'on' | 'off' | 'unknown';
  secondary?: string | null;
  icon?: string | null;
  /** Room/location tag emitted by the module so the dashboard can scope
   *  rendered items when a room tab other than "All"/"System" is active. */
  location?: string | null;
}

export interface ToggleListPayload {
  /** Raw English label — kept as fallback when `label_key` is missing or
   *  the active i18n bundle doesn't translate it. SDKs that ignore i18n
   *  can still render this directly. */
  label: string;
  /** Optional i18n key (`widgets.<module>.<slot>` convention) resolved by
   *  the template via `t(label_key, { defaultValue: label })`. */
  label_key?: string;
  /** Raw English summary string. Pre-formatted with counts. */
  summary?: string;
  /** Optional i18n key for the summary line. When present, the template
   *  passes `summary_args` as interpolation params and falls back to the
   *  raw `summary` if the key is missing. */
  summary_key?: string;
  summary_args?: Record<string, string | number>;
  items: ToggleItem[];
}

export default function ToggleListTemplate({ mod, activeRoom }: TemplateProps) {
  const { t } = useTranslation();
  const widget = mod.ui?.widget;
  const { data, loading, error, refetch } = useWidgetData<ToggleListPayload>({
    module: mod.name,
    key: 'state',
    events: widget?.refresh?.events,
    pollIntervalS: widget?.refresh?.poll_interval_s,
  });

  const [pending, setPending] = useState<Set<string>>(new Set());

  async function toggle(id: string) {
    if (pending.has(id)) return;
    setPending((p) => new Set(p).add(id));
    try {
      const r = await fetch(`/api/v1/modules/${mod.name}/action/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      if (!r.ok) throw new Error(await r.text());
      await refetch();
    } catch {
      const showToast = (await import('../../../store/useStore')).useStore.getState().showToast;
      showToast('Toggle failed', 'error');
    } finally {
      setPending((p) => {
        const n = new Set(p);
        n.delete(id);
        return n;
      });
    }
  }

  // Scope items to the active room tab. Sentinel rooms (`__all__`, `system`)
  // mean "no filter" — we still show everything. Items without a `location`
  // field always pass through (back-compat for modules that don't yet emit it).
  const items = useMemo(() => {
    if (!data) return [];
    if (!activeRoom || activeRoom === ALL_ROOM || activeRoom === SYSTEM_ROOM) {
      return data.items;
    }
    return data.items.filter((i) => {
      if (i.location == null || i.location === '') return true;
      return i.location === activeRoom;
    });
  }, [data, activeRoom]);

  if (loading && !data) return <ToggleListSkeleton />;
  if (error && !data) return <ErrorBlock onRetry={refetch} message={error} />;
  if (!data) return <ToggleListSkeleton />;

  const onCount = items.filter((i) => i.state === 'on').length;
  const label = resolveLabel(t, data.label, data.label_key);
  const rawSummary = data.summary ?? `${onCount} on`;
  const summary = resolveLabel(t, rawSummary, data.summary_key, data.summary_args);

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
        gap: 8,
      }}
    >
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'baseline',
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
        <div style={{ fontSize: 10, color: 'var(--tx3)', fontVariantNumeric: 'tabular-nums' }}>
          {summary}
        </div>
      </div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))',
        gap: 6,
        overflowY: 'auto',
        alignContent: 'start',
      }}>
        {items.map((item) => (
          <ToggleCell
            key={item.id}
            item={item}
            busy={pending.has(item.id)}
            onClick={() => toggle(item.id)}
            onDetail={() => useStore.getState().openDeviceDetail(item.id)}
          />
        ))}
        {items.length === 0 && (
          <div style={{ fontSize: 10.5, color: 'var(--tx3)', padding: '8px 0' }}>
            No items
          </div>
        )}
      </div>
    </motion.div>
  );
}

/** Long-press hold-time before opening the device-detail modal. 500ms
 *  is the iOS-standard threshold and gives users a clear distinction
 *  between "tap to toggle" and "hold to inspect". Right-click on
 *  desktop opens the same modal without waiting. */
const LONG_PRESS_MS = 500;

function ToggleCell({
  item, busy, onClick, onDetail,
}: {
  item: ToggleItem;
  busy: boolean;
  onClick: () => void;
  onDetail: () => void;
}) {
  const isOn = item.state === 'on';
  const isUnknown = item.state === 'unknown';

  // Long-press gating: pointerdown starts a timer; if the user lifts
  // before the timeout it's a click; if not, we mark `longPressed` and
  // suppress the upcoming click. ContextMenu (right-click) opens the
  // modal directly.
  const timerRef = useRef<number | null>(null);
  const longPressedRef = useRef(false);

  function clearTimer() {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }
  function onPointerDown(e: ReactPointerEvent) {
    if (busy || isUnknown) return;
    longPressedRef.current = false;
    // Only respond to primary button (left click / single-finger touch).
    if (e.button !== 0) return;
    clearTimer();
    timerRef.current = window.setTimeout(() => {
      longPressedRef.current = true;
      onDetail();
    }, LONG_PRESS_MS);
  }
  function onPointerUp() {
    clearTimer();
  }
  function onPointerCancel() {
    clearTimer();
    longPressedRef.current = false;
  }
  function onClickGuarded() {
    if (longPressedRef.current) {
      // Suppress the synthetic click that follows a long-press.
      longPressedRef.current = false;
      return;
    }
    onClick();
  }
  function onContextMenu(e: ReactMouseEvent) {
    e.preventDefault();
    if (busy || isUnknown) return;
    onDetail();
  }

  return (
    <motion.button
      onClick={onClickGuarded}
      onPointerDown={onPointerDown}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerCancel}
      onPointerCancel={onPointerCancel}
      onContextMenu={onContextMenu}
      disabled={busy || isUnknown}
      whileTap={{ scale: 0.96 }}
      transition={{ duration: 0.18, ease: [0.5, 1.4, 0.5, 1] }}
      style={{
        textAlign: 'left',
        padding: '8px 10px',
        borderRadius: 10,
        background: isOn ? 'color-mix(in srgb, var(--ac) 14%, var(--sf))' : 'var(--sf)',
        border: `1px solid ${isOn ? 'var(--ac)' : 'var(--b)'}`,
        color: 'var(--tx)',
        cursor: busy ? 'wait' : isUnknown ? 'default' : 'pointer',
        opacity: busy ? 0.6 : isUnknown ? 0.5 : 1,
        boxShadow: isOn ? 'var(--widget-glow-on)' : 'none',
        transition: 'background .15s, border-color .15s, box-shadow .15s',
        userSelect: 'none',
        touchAction: 'manipulation',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6, minWidth: 0 }}>
        <span style={{
          display: 'flex', alignItems: 'center', gap: 6,
          minWidth: 0, flex: 1,
        }}>
          {item.icon ? (
            <Icon name={item.icon} size={15} />
          ) : (
            <span aria-hidden style={{
              width: 8, height: 8, borderRadius: '50%',
              background: isOn ? 'var(--gr)' : 'var(--tx3)',
              flexShrink: 0,
            }} />
          )}
          <span style={{
            fontSize: 11.5,
            fontWeight: 600,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            color: isOn ? 'var(--tx)' : 'var(--tx2)',
          }}>
            {item.name}
          </span>
        </span>
        {isOn && (
          <span aria-hidden style={{
            width: 6, height: 6, borderRadius: '50%',
            background: 'var(--gr)',
            flexShrink: 0,
          }} />
        )}
      </div>
      {item.secondary && (
        <div style={{
          marginTop: 3,
          fontSize: 9.5,
          color: isOn ? 'var(--ac)' : 'var(--tx3)',
          fontVariantNumeric: 'tabular-nums',
        }}>
          {item.secondary}
        </div>
      )}
    </motion.button>
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
