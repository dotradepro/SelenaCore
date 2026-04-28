import { useState } from 'react';
import { motion } from 'motion/react';
import { useWidgetData } from '../../../hooks/useWidgetData';
import { ToggleListSkeleton } from './Skeleton';
import Icon from './Icon';
import type { TemplateProps } from './registry';

export interface ToggleItem {
  id: string;
  name: string;
  state: 'on' | 'off' | 'unknown';
  secondary?: string | null;
  icon?: string | null;
}

export interface ToggleListPayload {
  label: string;
  summary?: string;
  items: ToggleItem[];
}

export default function ToggleListTemplate({ mod }: TemplateProps) {
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

  if (loading && !data) return <ToggleListSkeleton />;
  if (error && !data) return <ErrorBlock onRetry={refetch} message={error} />;
  if (!data) return <ToggleListSkeleton />;

  const onCount = data.items.filter((i) => i.state === 'on').length;
  const summary = data.summary ?? `${onCount} on`;

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
          {data.label}
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
        {data.items.map((item) => (
          <ToggleCell
            key={item.id}
            item={item}
            busy={pending.has(item.id)}
            onClick={() => toggle(item.id)}
          />
        ))}
        {data.items.length === 0 && (
          <div style={{ fontSize: 10.5, color: 'var(--tx3)', padding: '8px 0' }}>
            No items
          </div>
        )}
      </div>
    </motion.div>
  );
}

function ToggleCell({
  item, busy, onClick,
}: { item: ToggleItem; busy: boolean; onClick: () => void }) {
  const isOn = item.state === 'on';
  const isUnknown = item.state === 'unknown';
  return (
    <motion.button
      onClick={onClick}
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
