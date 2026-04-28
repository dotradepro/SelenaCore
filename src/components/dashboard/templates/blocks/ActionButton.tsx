import { useState } from 'react';
import Icon from '../Icon';
import { useStore } from '../../../../store/useStore';

export interface ActionSpec {
  /** Logical key — must match a manifest ``ui.widget.actions[key]``. */
  id: string;
  label: string;
  icon?: string | null;
  /** Optional body to send as JSON; bare string means simple ``{id: <id>}``
   *  bodies (which is what most actions expect). */
  body?: Record<string, unknown>;
  tone?: 'neutral' | 'info' | 'ok' | 'warn' | 'alert';
}

export interface ActionButtonProps {
  module: string;
  spec: ActionSpec;
  /** Called after the action succeeds — typically the host template's
   *  ``refetch`` so the UI snaps to the new state immediately. */
  onSuccess?: () => void | Promise<void>;
}

const TONE_BG: Record<NonNullable<ActionSpec['tone']>, string> = {
  neutral: 'var(--sf2)',
  info: 'var(--ac)',
  ok: 'var(--gr)',
  warn: 'var(--am)',
  alert: 'var(--rd)',
};

const TONE_FG: Record<NonNullable<ActionSpec['tone']>, string> = {
  neutral: 'var(--tx)',
  info: 'var(--on-accent)',
  ok: 'var(--on-success)',
  warn: 'var(--on-warning)',
  alert: 'var(--on-danger)',
};

/** Inline action button. POSTs to ``/api/v1/modules/{module}/action/{id}``,
 *  shows a busy state while in-flight, surfaces failures via the global
 *  toast. Used by Status/Weather to invoke side-effects (refresh,
 *  apply-update, run-scene). */
export default function ActionButton({ module, spec, onSuccess }: ActionButtonProps) {
  const [busy, setBusy] = useState(false);
  const tone = spec.tone ?? 'neutral';
  const bg = TONE_BG[tone];
  const fg = TONE_FG[tone];

  async function run() {
    if (busy) return;
    setBusy(true);
    try {
      const r = await fetch(`/api/v1/modules/${module}/action/${spec.id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(spec.body ?? {}),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      if (onSuccess) await onSuccess();
    } catch (e) {
      const showToast = useStore.getState().showToast;
      showToast(`${spec.label} failed`, 'error');
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      onClick={run}
      disabled={busy}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '4px 10px',
        borderRadius: 6,
        background: bg,
        color: fg,
        border: tone === 'neutral' ? '1px solid var(--b)' : `1px solid ${bg}`,
        fontSize: 10,
        fontWeight: 600,
        cursor: busy ? 'wait' : 'pointer',
        opacity: busy ? 0.6 : 1,
        transition: 'background .15s, opacity .15s',
      }}
    >
      {spec.icon && <Icon name={spec.icon} size={11} color={fg} />}
      {spec.label}
    </button>
  );
}
