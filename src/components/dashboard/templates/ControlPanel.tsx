import { useState } from 'react';
import { motion } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { useWidgetData } from '../../../hooks/useWidgetData';
import { ControlPanelSkeleton } from './Skeleton';
import { useStore } from '../../../store/useStore';
import IconStrip, { type IconStripItem } from './blocks/IconStrip';
import { resolveLabel } from './i18n';
import type { TemplateProps } from './registry';

export interface ControlPanelPayload {
  /** Raw English label, kept as fallback when `label_key` is missing. */
  label: string;
  label_key?: string;
  /** Optional interpolation params for `label_key` (e.g. `{location: 'Living'}`). */
  label_args?: Record<string, string | number>;
  primary: {
    value: string;
    unit?: string;
    /** Raw English secondary line (e.g. "→ set 23.0°"); fallback for secondary_key. */
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

export default function ControlPanelTemplate({ mod }: TemplateProps) {
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

  async function setMode(id: string) {
    if (pendingMode) return;
    setPendingMode(id);
    try {
      const r = await fetch(`/api/v1/modules/${mod.name}/action/set_mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
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
    if (pendingStepper) return;
    setPendingStepper(id);
    try {
      const r = await fetch(`/api/v1/modules/${mod.name}/action/step`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, value }),
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
  if (!data) return <ControlPanelSkeleton />;

  const label = resolveLabel(t, data.label, data.label_key, data.label_args);
  const primarySecondary = resolveLabel(
    t, data.primary.secondary, data.primary.secondary_key, data.primary.secondary_args,
  );
  const modeOptions = data.modes?.options.map((o) => ({
    ...o,
    label: resolveLabel(t, o.label, o.label_key),
  }));
  const stepperSpecs = data.steppers?.map((s) => ({
    ...s,
    label: resolveLabel(t, s.label, s.label_key),
  }));

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
        fontSize: 9.5,
        fontWeight: 600,
        color: 'var(--tx3)',
        textTransform: 'uppercase',
        letterSpacing: '.08em',
      }}>
        {label}
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
            {data.primary.value}
          </span>
          {data.primary.unit && (
            <span style={{ fontSize: 16, color: 'var(--tx2)', fontWeight: 300 }}>
              {data.primary.unit}
            </span>
          )}
        </div>
        {primarySecondary && (
          <div style={{ fontSize: 10.5, color: 'var(--tx3)', marginTop: 4 }}>
            {primarySecondary}
          </div>
        )}
      </div>

      {data.secondary_pills && data.secondary_pills.length > 0 && (
        <IconStrip items={data.secondary_pills} />
      )}

      {data.modes && modeOptions && modeOptions.length > 0 && (
        <ModeRow
          options={modeOptions}
          current={data.modes.current}
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
