import { useState } from 'react';
import { motion } from 'motion/react';
import { useWidgetData } from '../../../hooks/useWidgetData';
import { ControlPanelSkeleton } from './Skeleton';
import { useStore } from '../../../store/useStore';
import type { TemplateProps } from './registry';

/** Payload shape — see docs/dashboard-recraft.md §3.3.4. */
export interface ControlPanelPayload {
  label: string;
  primary: { value: string; unit?: string; secondary?: string };
  modes?: {
    current: string;
    options: { id: string; label: string }[];
  };
  steppers?: {
    id: string;
    label: string;
    value: string;
    unit?: string;
    min?: number;
    max?: number;
    step?: number;
  }[];
}

export default function ControlPanelTemplate({ mod }: TemplateProps) {
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

  return (
    <div style={{
      width: '100%', height: '100%',
      padding: '10px 14px 12px',
      display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--tx3)', textTransform: 'uppercase', letterSpacing: '.05em' }}>
        {data.label}
      </div>
      <div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span style={{ fontSize: 28, fontWeight: 600, color: 'var(--tx)', lineHeight: 1 }}>
            {data.primary.value}
          </span>
          {data.primary.unit && (
            <span style={{ fontSize: 13, color: 'var(--tx2)' }}>{data.primary.unit}</span>
          )}
        </div>
        {data.primary.secondary && (
          <div style={{ fontSize: 10, color: 'var(--tx3)', marginTop: 2 }}>{data.primary.secondary}</div>
        )}
      </div>

      {data.modes && data.modes.options.length > 0 && (
        <ModeRow
          options={data.modes.options}
          current={data.modes.current}
          pending={pendingMode}
          onSelect={setMode}
        />
      )}

      {data.steppers && data.steppers.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 'auto' }}>
          {data.steppers.map((s) => (
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
    </div>
  );
}

function ModeRow({ options, current, pending, onSelect }: {
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
            transition={{ duration: 0.18, ease: [0.5, 1.4, 0.5, 1] }}
            style={{
              padding: '6px 4px',
              borderRadius: 6,
              fontSize: 10,
              fontWeight: 500,
              cursor: busy ? 'wait' : 'pointer',
              opacity: busy ? 0.6 : 1,
              background: active ? 'var(--ac)' : 'var(--sf)',
              color: active ? 'var(--on-accent)' : 'var(--tx2)',
              border: `1px solid ${active ? 'var(--ac)' : 'var(--b)'}`,
              boxShadow: active ? 'var(--widget-glow-on)' : 'none',
              transition: 'background .15s, border-color .15s, color .15s',
            }}
          >
            {o.label}
          </motion.button>
        );
      })}
    </div>
  );
}

function StepperRow({ spec, busy, onStep }: {
  spec: ControlPanelPayload['steppers'] extends (infer T)[] | undefined ? T : never;
  busy: boolean;
  onStep: (delta: 1 | -1) => void;
}) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '4px 6px', borderRadius: 8,
      background: 'var(--sf2)', border: '1px solid var(--b)',
      opacity: busy ? 0.6 : 1,
    }}>
      <span style={{ fontSize: 10, color: 'var(--tx3)' }}>{spec.label}</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <StepBtn label="−" onClick={() => onStep(-1)} disabled={busy} />
        <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--tx)', minWidth: 36, textAlign: 'center' }}>
          {spec.value}{spec.unit ?? ''}
        </span>
        <StepBtn label="+" onClick={() => onStep(1)} disabled={busy} />
      </div>
    </div>
  );
}

function StepBtn({ label, onClick, disabled }: { label: string; onClick: () => void; disabled: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        width: 22, height: 22, borderRadius: 6,
        background: 'var(--sf)', color: 'var(--tx2)',
        border: '1px solid var(--b)',
        fontSize: 14, lineHeight: 1,
        cursor: disabled ? 'wait' : 'pointer',
      }}
    >
      {label}
    </button>
  );
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(Math.max(n, lo), hi);
}

function ErrorBlock({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
      <div style={{ fontSize: 11, color: 'var(--rd)' }}>Unavailable</div>
      <button
        onClick={onRetry}
        title={message}
        style={{ fontSize: 10, padding: '3px 10px', borderRadius: 6, background: 'var(--sf2)', color: 'var(--tx2)', border: '1px solid var(--b)', cursor: 'pointer' }}
      >Retry</button>
    </div>
  );
}
