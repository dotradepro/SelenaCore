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
  /** Optional power toggle. Renders as a prominent on/off button in
   *  the header. Climate devices use this to expose the underlying
   *  `state.on` flag separately from the heat/cool mode selector — a
   *  device can be off while still tracking a mode it'll resume on. */
  power?: { on: boolean };
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
  /** Optional second segmented control (separate from `modes`). Climate
   *  uses this for fan-speed (Auto/Low/Med/High); the data shape is
   *  identical to `modes` so the same renderer handles both. */
  fan_speed?: {
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
  /** Optional toggle bank rendered below the main controls. Each entry
   *  is a labelled boolean; tapping flips it via `set_state` action.
   *  Climate uses this for sleep/turbo/quiet/eco/health/light flags;
   *  drivers that don't expose a flag simply omit it. */
  flags?: {
    id: string;
    label: string;
    label_key?: string;
    on: boolean;
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
  const [pendingFan, setPendingFan] = useState<string | null>(null);
  const [pendingPower, setPendingPower] = useState(false);
  const [pendingFlag, setPendingFlag] = useState<string | null>(null);
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

  /** Generic state-patch action — used for power on/off, fan speed,
   *  and boolean climate flags. Funnels through the climate widget's
   *  ``set_state`` endpoint which validates the patch keys against an
   *  allow-list before forwarding to the device driver. */
  async function setState(patch: Record<string, unknown>): Promise<boolean> {
    if (!current) return false;
    const body: Record<string, unknown> = { patch };
    if (current.device_id) body.device_id = current.device_id;
    try {
      const r = await fetch(`/api/v1/modules/${mod.name}/action/set_state`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refetch();
      return true;
    } catch {
      useStore.getState().showToast('Action failed', 'error');
      return false;
    }
  }

  async function togglePower() {
    if (pendingPower || !current?.power) return;
    setPendingPower(true);
    try { await setState({ on: !current.power.on }); } finally { setPendingPower(false); }
  }
  async function setFanSpeed(id: string) {
    if (pendingFan) return;
    setPendingFan(id);
    try { await setState({ fan_speed: id }); } finally { setPendingFan(null); }
  }
  async function toggleFlag(flagId: string, currentValue: boolean) {
    if (pendingFlag) return;
    setPendingFlag(flagId);
    try { await setState({ [flagId]: !currentValue }); } finally { setPendingFlag(null); }
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
  const fanOptions = current.fan_speed?.options.map((o) => ({
    ...o,
    label: resolveLabel(t, o.label, o.label_key),
  }));
  const stepperSpecs = current.steppers?.map((s) => ({
    ...s,
    label: resolveLabel(t, s.label, s.label_key),
  }));
  const flagItems = current.flags?.map((f) => ({
    ...f,
    label: resolveLabel(t, f.label, f.label_key),
  }));

  const showCarousel = rooms.length > 1;
  const goPrev = () => setCarouselIndex((i) => (i - 1 + rooms.length) % rooms.length);
  const goNext = () => setCarouselIndex((i) => (i + 1) % rooms.length);
  // When power is off, dim everything but the toggle so users see at a
  // glance that the device isn't actively running. Doesn't disable
  // controls — they may still want to set a target temp before flipping
  // power on.
  const isPoweredOff = current.power?.on === false;

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
      {/* Header — label + (when multi-room) carousel switcher + power
          toggle. Power lives in the header rather than near the modes
          because it's the most-tapped control and should be reachable
          without scrolling on small widget cells. */}
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
        {current.power && (
          <PowerButton on={current.power.on} busy={pendingPower} onClick={togglePower} />
        )}
      </div>

      {/* Primary readout (current temp / current value) — dimmed when
          the device is powered off, since the reading is stale. */}
      <div style={{ opacity: isPoweredOff ? 0.55 : 1, transition: 'opacity .15s' }}>
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

      {/* Fan speed selector — same shape as modes, just routes to the
          generic state-patch action instead of `set_mode`. */}
      {current.fan_speed && fanOptions && fanOptions.length > 0 && (
        <ModeRow
          options={fanOptions}
          current={current.fan_speed.current}
          pending={pendingFan}
          onSelect={setFanSpeed}
        />
      )}

      {stepperSpecs && stepperSpecs.length > 0 && (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
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

      {/* Climate flag bank — sleep / turbo / quiet / eco / health /
          light. Rendered as a 2- or 3-column toggle grid depending on
          available width; flag count typically 4–6 so the grid stays
          balanced. */}
      {flagItems && flagItems.length > 0 && (
        <div style={{ marginTop: 'auto' }}>
          <FlagBank
            flags={flagItems}
            pendingId={pendingFlag}
            onToggle={(id, on) => toggleFlag(id, on)}
          />
        </div>
      )}
    </motion.div>
  );
}

/** Power on/off toggle in the widget header. Visually distinct from the
 *  mode-segmented row because it's the master power switch — when it's
 *  off, mode/temp settings are advisory only (the device resumes them
 *  on power-on). Glow + accent fill in `on` state mirrors how lights
 *  show themselves "active". */
function PowerButton({
  on, busy, onClick,
}: { on: boolean; busy: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      type="button"
      title={on ? 'Power off' : 'Power on'}
      aria-label={on ? 'Power off' : 'Power on'}
      style={{
        width: 26, height: 26,
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        borderRadius: 999,
        border: `1px solid ${on ? 'var(--ac)' : 'var(--b)'}`,
        background: on ? 'var(--ac)' : 'var(--sf2)',
        color: on ? 'var(--on-accent)' : 'var(--tx2)',
        cursor: busy ? 'wait' : 'pointer',
        opacity: busy ? 0.6 : 1,
        fontSize: 13, lineHeight: 1, padding: 0,
        boxShadow: on ? 'var(--widget-glow-on)' : 'none',
        transition: 'background .15s, border-color .15s, box-shadow .15s',
      }}
    >
      ⏻
    </button>
  );
}

/** Compact 2- or 3-column toggle grid for boolean device flags
 *  (sleep / turbo / quiet / eco / health / light on Gree-class climate
 *  units). Adapts column count to the number of flags so a 2-flag bank
 *  doesn't render 4 awkward half-empty cells. */
function FlagBank({
  flags, pendingId, onToggle,
}: {
  flags: { id: string; label: string; on: boolean }[];
  pendingId: string | null;
  onToggle: (id: string, currentOn: boolean) => void;
}) {
  const cols = flags.length <= 3 ? flags.length : flags.length <= 4 ? 2 : 3;
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 4,
    }}>
      {flags.map((f) => {
        const busy = pendingId === f.id;
        return (
          <button
            key={f.id}
            onClick={() => onToggle(f.id, f.on)}
            disabled={busy}
            type="button"
            style={{
              padding: '5px 8px',
              borderRadius: 7,
              fontSize: 10, fontWeight: 500,
              background: f.on
                ? 'color-mix(in srgb, var(--ac) 14%, var(--sf))'
                : 'var(--sf)',
              border: `1px solid ${f.on ? 'var(--ac)' : 'var(--b)'}`,
              color: f.on ? 'var(--tx)' : 'var(--tx2)',
              cursor: busy ? 'wait' : 'pointer',
              opacity: busy ? 0.6 : 1,
              boxShadow: f.on ? 'var(--widget-glow-on)' : 'none',
              transition: 'background .15s, border-color .15s, box-shadow .15s',
              textAlign: 'center',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {f.label}
          </button>
        );
      })}
    </div>
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
