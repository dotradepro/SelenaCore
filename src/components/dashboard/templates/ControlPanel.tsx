import { useEffect, useMemo, useState } from 'react';
import { motion } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { useWidgetData } from '../../../hooks/useWidgetData';
import { ControlPanelSkeleton } from './Skeleton';
import { useStore } from '../../../store/useStore';
import IconStrip, { type IconStripItem } from './blocks/IconStrip';
import { resolveLabel } from './i18n';
import { ALL_ROOM, SYSTEM_ROOM } from '../RoomTabs';
import type { TemplateProps } from './registry';

/** One ControlPanel payload — either standalone (single-device install)
 *  or one slide of the multi-room carousel. The shape is identical so a
 *  single renderer handles both modes. */
export interface ControlPanelRoomPayload {
  /** Backend device id — echoed back in action bodies so multi-device
   *  installs target the right device. Older single-device payloads
   *  omit this; actions then fall through to the backend's primary. */
  device_id?: string;
  /** Room/location of this slide (drives the active-room→slide jump). */
  room?: string | null;
  label: string;
  label_key?: string;
  label_args?: Record<string, string | number>;
  primary: {
    value: string;
    unit?: string;
    secondary?: string;
    secondary_key?: string;
    secondary_args?: Record<string, string | number>;
  };
  modes?: {
    current: string;
    options: { id: string; label: string; label_key?: string }[];
  };
  steppers?: {
    id: string;
    label: string;
    label_key?: string;
    value: string;
    unit?: string;
    min?: number;
    max?: number;
    step?: number;
  }[];
  secondary_pills?: IconStripItem[];
}

/** Top-level payload — extends the single-room shape with an optional
 *  `rooms` array. Backends with one device emit just the primary fields;
 *  multi-device installs additionally include `rooms: [...]` so the
 *  template renders a carousel switcher. */
export interface ControlPanelPayload extends ControlPanelRoomPayload {
  rooms?: ControlPanelRoomPayload[];
}

export default function ControlPanelTemplate({ mod, activeRoom }: TemplateProps) {
  const { t } = useTranslation();
  const widget = mod.ui?.widget;
  const { data, loading, error, refetch } = useWidgetData<ControlPanelPayload>({
    module: mod.name,
    key: 'state',
    events: widget?.refresh?.events,
    pollIntervalS: widget?.refresh?.poll_interval_s,
  });

  const [pendingMode, setPendingMode] = useState<string | null>(null);
  const [pendingStepper, setPendingStepper] = useState<string | null>(null);
  const [carouselIndex, setCarouselIndex] = useState(0);

  // Multi-room rooms list (or a single-element array for single-device installs).
  // `data.rooms` is present when the backend emits a carousel; otherwise we
  // wrap the top-level payload as a single-slide list so the renderer is
  // uniform.
  const rooms: ControlPanelRoomPayload[] = useMemo(() => {
    if (!data) return [];
    if (data.rooms && data.rooms.length > 0) return data.rooms;
    return [data];
  }, [data]);

  // When the user picks a specific room tab, jump the carousel to that
  // slide. ALL/SYSTEM keep the user-driven index (or default to 0).
  useEffect(() => {
    if (rooms.length <= 1) return;
    if (!activeRoom || activeRoom === ALL_ROOM || activeRoom === SYSTEM_ROOM) return;
    const idx = rooms.findIndex((r) => r.room === activeRoom);
    if (idx >= 0) setCarouselIndex(idx);
  }, [activeRoom, rooms]);

  // Clamp index when rooms count shrinks (e.g., a device gets unpaired).
  useEffect(() => {
    if (carouselIndex >= rooms.length) setCarouselIndex(Math.max(0, rooms.length - 1));
  }, [rooms.length, carouselIndex]);

  const current: ControlPanelRoomPayload | undefined = rooms[carouselIndex];

  async function setMode(id: string) {
    if (pendingMode || !current) return;
    setPendingMode(id);
    try {
      const body: Record<string, unknown> = { id };
      if (current.device_id) body.device_id = current.device_id;
      const r = await fetch(`/api/v1/modules/${mod.name}/action/set_mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refetch();
    } catch {
      useStore.getState().showToast('Mode change failed', 'error');
    } finally {
      setPendingMode(null);
    }
  }

  async function step(id: string, value: number) {
    if (pendingStepper || !current) return;
    setPendingStepper(id);
    try {
      const body: Record<string, unknown> = { id, value };
      if (current.device_id) body.device_id = current.device_id;
      const r = await fetch(`/api/v1/modules/${mod.name}/action/step`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refetch();
    } catch {
      useStore.getState().showToast('Step failed', 'error');
    } finally {
      setPendingStepper(null);
    }
  }

  if (loading && !data) return <ControlPanelSkeleton />;
  if (error && !data) return <ErrorBlock onRetry={refetch} message={error} />;
  if (!data || !current) return <ControlPanelSkeleton />;

  const label = resolveLabel(t, current.label, current.label_key, current.label_args);
  const primarySecondary = resolveLabel(
    t, current.primary.secondary, current.primary.secondary_key, current.primary.secondary_args,
  );
  const modeOptions = current.modes?.options.map((o) => ({
    ...o,
    label: resolveLabel(t, o.label, o.label_key),
  }));
  const stepperSpecs = current.steppers?.map((s) => ({
    ...s,
    label: resolveLabel(t, s.label, s.label_key),
  }));

  const showCarousel = rooms.length > 1;
  const goPrev = () => setCarouselIndex((i) => (i - 1 + rooms.length) % rooms.length);
  const goNext = () => setCarouselIndex((i) => (i + 1) % rooms.length);

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
      {/* Header — label + (when multi-room) carousel switcher with
          ◄ arrow / current room / ► arrow + dot indicator below. The
          dots are rendered inline rather than in a separate row to save
          vertical space; for >5 rooms they collapse to "n/N". */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 6,
      }}>
        <div style={{
          fontSize: 9.5,
          fontWeight: 600,
          color: 'var(--tx3)',
          textTransform: 'uppercase',
          letterSpacing: '.08em',
          flex: 1,
          minWidth: 0,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}>
          {label}
        </div>
        {showCarousel && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
            <CarouselButton onClick={goPrev} glyph="‹" />
            <span style={{
              fontSize: 9, color: 'var(--tx3)',
              fontVariantNumeric: 'tabular-nums', minWidth: 22,
              textAlign: 'center',
            }}>
              {carouselIndex + 1}/{rooms.length}
            </span>
            <CarouselButton onClick={goNext} glyph="›" />
          </div>
        )}
      </div>

      <div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
          <span style={{
            fontSize: 38,
            fontWeight: 300,
            color: 'var(--tx)',
            lineHeight: 1,
            letterSpacing: '-.02em',
            fontVariantNumeric: 'tabular-nums',
          }}>
            {current.primary.value}
          </span>
          {current.primary.unit && (
            <span style={{ fontSize: 16, color: 'var(--tx2)', fontWeight: 300 }}>
              {current.primary.unit}
            </span>
          )}
        </div>
        {primarySecondary && (
          <div style={{ fontSize: 10.5, color: 'var(--tx3)', marginTop: 4 }}>
            {primarySecondary}
          </div>
        )}
      </div>

      {current.secondary_pills && current.secondary_pills.length > 0 && (
        <IconStrip items={current.secondary_pills} />
      )}

      {current.modes && modeOptions && modeOptions.length > 0 && (
        <ModeRow
          options={modeOptions}
          current={current.modes.current}
          pending={pendingMode}
          onSelect={setMode}
        />
      )}

      {stepperSpecs && stepperSpecs.length > 0 && (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
          marginTop: 'auto',
        }}>
          {stepperSpecs.map((s) => (
            <StepperRow
              key={s.id}
              spec={s}
              busy={pendingStepper === s.id}
              onStep={(delta) => {
                const cur = parseFloat(s.value);
                const stepSize = s.step ?? 1;
                const next = cur + delta * stepSize;
                const clamped = clamp(next, s.min ?? -Infinity, s.max ?? Infinity);
                step(s.id, clamped);
              }}
            />
          ))}
        </div>
      )}
    </motion.div>
  );
}

/** Tiny round button used by the carousel header. Kept as a separate
 *  component so the touch target stays a clean 22×22px square that
 *  doesn't inherit anything from the surrounding flex container. */
function CarouselButton({ onClick, glyph }: { onClick: () => void; glyph: string }) {
  return (
    <button
      onClick={onClick}
      type="button"
      style={{
        width: 22, height: 22,
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        borderRadius: 6,
        border: '1px solid var(--b)',
        background: 'var(--sf)',
        color: 'var(--tx2)',
        cursor: 'pointer',
        fontSize: 14, lineHeight: 1, fontWeight: 600,
        padding: 0,
      }}
    >
      {glyph}
    </button>
  );
}

function ModeRow({
  options, current, pending, onSelect,
}: {
  options: { id: string; label: string }[];
  current: string;
  pending: string | null;
  onSelect: (id: string) => void;
}) {
  const cols = options.length <= 4 ? options.length : 2;
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: `repeat(${cols}, 1fr)`,
      gap: 4,
      padding: 3,
      borderRadius: 8,
      background: 'var(--sf2)',
      border: '1px solid var(--b)',
    }}>
      {options.map((o) => {
        const active = o.id === current;
        const busy = pending === o.id;
        return (
          <motion.button
            key={o.id}
            onClick={() => onSelect(o.id)}
            disabled={busy}
            whileTap={{ scale: 0.96 }}
            transition={{ duration: 0.15, ease: [0.5, 1.4, 0.5, 1] }}
            style={{
              padding: '6px 4px',
              borderRadius: 6,
              fontSize: 10,
              fontWeight: 600,
              cursor: busy ? 'wait' : 'pointer',
              opacity: busy ? 0.6 : 1,
              background: active ? 'var(--ac)' : 'transparent',
              color: active ? 'var(--on-accent)' : 'var(--tx2)',
              border: 'none',
              boxShadow: active ? 'var(--widget-glow-on)' : 'none',
              transition: 'background .15s, color .15s',
            }}
          >
            {o.label}
          </motion.button>
        );
      })}
    </div>
  );
}

function StepperRow({
  spec, busy, onStep,
}: {
  spec: NonNullable<ControlPanelPayload['steppers']>[number];
  busy: boolean;
  onStep: (delta: 1 | -1) => void;
}) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '5px 8px',
      borderRadius: 8,
      background: 'var(--sf2)',
      border: '1px solid var(--b)',
      opacity: busy ? 0.6 : 1,
    }}>
      <span style={{ fontSize: 10, color: 'var(--tx3)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.05em' }}>
        {spec.label}
      </span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <StepBtn label="−" onClick={() => onStep(-1)} disabled={busy} />
        <span style={{
          fontSize: 12,
          fontWeight: 600,
          color: 'var(--tx)',
          minWidth: 42,
          textAlign: 'center',
          fontVariantNumeric: 'tabular-nums',
        }}>
          {spec.value}{spec.unit ?? ''}
        </span>
        <StepBtn label="+" onClick={() => onStep(1)} disabled={busy} />
      </div>
    </div>
  );
}

function StepBtn({ label, onClick, disabled }: { label: string; onClick: () => void; disabled: boolean }) {
  return (
    <motion.button
      onClick={onClick}
      disabled={disabled}
      whileTap={{ scale: 0.92 }}
      style={{
        width: 26, height: 26, borderRadius: 6,
        background: 'var(--sf)', color: 'var(--tx)',
        border: '1px solid var(--b)',
        fontSize: 14, fontWeight: 500, lineHeight: 1,
        cursor: disabled ? 'wait' : 'pointer',
      }}
    >
      {label}
    </motion.button>
  );
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(Math.max(n, lo), hi);
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
