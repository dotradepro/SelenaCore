import { useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { useWidgetData } from '../../../hooks/useWidgetData';
import { useStore } from '../../../store/useStore';
import { ToggleListSkeleton } from './Skeleton';
import Icon from './Icon';
import { resolveLabel } from './i18n';
import DeviceCapabilities from './DeviceCapabilities';
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

  // Drill-in state: when a user taps an item, we replace the list with
  // that device's full capability panel inside the same widget. When
  // there's only one item to begin with, we skip the list entirely and
  // open in detail view — the list view of a one-item list is wasted
  // chrome. Resets when the active room changes (room switch implies a
  // different scope of devices).
  const [selectedId, setSelectedId] = useState<string | null>(null);
  useEffect(() => { setSelectedId(null); }, [activeRoom]);

  // If the selected device gets filtered out (room switch, item removed
  // upstream), drop selection so we don't render against a stale id.
  useEffect(() => {
    if (selectedId && !items.some((i) => i.id === selectedId)) {
      setSelectedId(null);
    }
  }, [items, selectedId]);

  // Auto-detail rule: a single-item list is just an extra tap before
  // the user can do anything useful. Open detail directly.
  const autoDetail = items.length === 1;
  const detailItemId = autoDetail ? items[0]?.id : selectedId;
  const inDetail = !!detailItemId;

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
      <AnimatePresence mode="wait">
        {inDetail && detailItemId ? (
          <DetailView
            key="detail"
            deviceId={detailItemId}
            // Only show the back button when the detail view is something
            // the user explicitly drilled into — auto-detail (single-item
            // widget) has nothing to go back to.
            onBack={autoDetail ? null : () => setSelectedId(null)}
          />
        ) : (
          <ListView
            key="list"
            label={label}
            summary={summary}
            items={items}
            onPick={setSelectedId}
          />
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// ── List view ────────────────────────────────────────────────────────────

function ListView({
  label, summary, items, onPick,
}: {
  label: string;
  summary: string;
  items: ToggleItem[];
  onPick: (id: string) => void;
}) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15 }}
      style={{ display: 'flex', flexDirection: 'column', gap: 8, height: '100%' }}
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
          <ToggleCell key={item.id} item={item} onClick={() => onPick(item.id)} />
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

// ── Detail view (drill-in or single-item auto-detail) ────────────────────

function DetailView({
  deviceId, onBack,
}: {
  deviceId: string;
  /** ``null`` → suppress the back button (single-item widget — there's
   *  no list to go back to). */
  onBack: (() => void) | null;
}) {
  const { t } = useTranslation();
  // Pull the live device from the store so capability changes
  // round-trip via the existing WebSocket and re-render automatically.
  const device = useStore((s) => s.devices.find((d) => d.device_id === deviceId) ?? null);
  const updateDeviceState = useStore((s) => s.updateDeviceState);

  if (!device) {
    // Selected item exists in the toggle-list payload but we don't have
    // the device row in the store yet (race during initial fetch). Show
    // a tight placeholder so the panel doesn't flash.
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      >
        <div style={{ fontSize: 10.5, color: 'var(--tx3)' }}>
          {t('common.loading', { defaultValue: 'Loading...' })}
        </div>
      </motion.div>
    );
  }

  const onChange = (patch: Record<string, unknown>) => updateDeviceState(deviceId, patch);

  return (
    <motion.div
      initial={{ opacity: 0, x: 8 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -8 }}
      transition={{ duration: 0.18 }}
      style={{ display: 'flex', flexDirection: 'column', gap: 10, height: '100%', overflowY: 'auto' }}
    >
      {/* Header: back chevron + device name. Pinned to the top so the
          user always has a way out of detail view, even when the
          capability panel scrolls. */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0,
      }}>
        {onBack && (
          <button
            onClick={onBack}
            aria-label={t('common.back', { defaultValue: 'Back' })}
            style={{
              width: 26, height: 26, borderRadius: 6,
              background: 'var(--sf2)', border: '1px solid var(--b)',
              color: 'var(--tx2)', cursor: 'pointer',
              fontSize: 14, lineHeight: 1, padding: 0,
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            ‹
          </button>
        )}
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{
            fontSize: 12.5, fontWeight: 600, color: 'var(--tx)',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {device.name}
          </div>
          {(device.room ?? device.location) && (
            <div style={{ fontSize: 9.5, color: 'var(--tx3)', marginTop: 1 }}>
              {device.room ?? device.location}
            </div>
          )}
        </div>
      </div>
      <DeviceCapabilities device={device} onChange={onChange} compact />
    </motion.div>
  );
}

// ── Toggle cell (list item) ──────────────────────────────────────────────

function ToggleCell({
  item, onClick,
}: {
  item: ToggleItem;
  onClick: () => void;
}) {
  const isOn = item.state === 'on';
  const isUnknown = item.state === 'unknown';
  return (
    <motion.button
      onClick={onClick}
      disabled={isUnknown}
      whileTap={{ scale: 0.96 }}
      transition={{ duration: 0.18, ease: [0.5, 1.4, 0.5, 1] }}
      style={{
        textAlign: 'left',
        padding: '8px 10px',
        borderRadius: 10,
        background: isOn ? 'color-mix(in srgb, var(--ac) 14%, var(--sf))' : 'var(--sf)',
        border: `1px solid ${isOn ? 'var(--ac)' : 'var(--b)'}`,
        color: 'var(--tx)',
        cursor: isUnknown ? 'default' : 'pointer',
        opacity: isUnknown ? 0.5 : 1,
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
