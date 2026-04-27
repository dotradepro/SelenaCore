import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useStore, type Module } from '../../store/useStore';

/** Bottom-sheet drawer for pinning new widgets to the V2 dashboard.
 *  Lists every module that declares a ``ui.widget`` block (template- or
 *  custom-kind) and is not currently pinned. Click to pin via the
 *  existing ``pinModule`` store action — same persistence path V1 used,
 *  so toggles propagate to other kiosks via SyncManager. */
export interface AddWidgetDrawerProps {
  modules: Module[];
  pinned: string[];
  onClose: () => void;
}

export default function AddWidgetDrawer({ modules, pinned, onClose }: AddWidgetDrawerProps) {
  const { t } = useTranslation();
  const pinModule = useStore((s) => s.pinModule);

  // Available = has a widget block AND not pinned. We don't filter by
  // status — stopped modules can still be pinned; the dashboard will
  // render the placeholder until they come back up.
  const available = modules.filter(
    (m) => !!m.ui?.widget && !pinned.includes(m.name),
  );

  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0, zIndex: 40,
          background: 'rgba(0, 0, 0, .45)',
        }}
      />
      <div
        role="dialog"
        aria-label={t('dashboard.addWidget') as string}
        style={{
          position: 'fixed', bottom: 0, left: 0, right: 0,
          zIndex: 50,
          background: 'var(--sf)',
          borderRadius: '14px 14px 0 0',
          padding: '12px 14px 20px',
          boxShadow: '0 -4px 24px rgba(0, 0, 0, .3)',
          maxHeight: '60vh',
          overflowY: 'auto',
        }}
      >
        <div style={{ width: 36, height: 4, borderRadius: 2, background: 'var(--sf3)', margin: '0 auto 12px' }} />

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--tx)' }}>
            {t('dashboard.addWidget')}
          </div>
          <button
            onClick={onClose}
            style={{ fontSize: 11, color: 'var(--tx3)', background: 'transparent', border: 'none', cursor: 'pointer' }}
          >
            ✕
          </button>
        </div>

        {available.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '20px 0', color: 'var(--tx3)', fontSize: 11 }}>
            {t('dashboard.allPinned')}
          </div>
        ) : (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
            gap: 8,
          }}>
            {available.map((mod) => (
              <ModuleCard key={mod.name} mod={mod} onPin={() => { pinModule(mod.name); onClose(); }} />
            ))}
          </div>
        )}
      </div>
    </>
  );
}

function ModuleCard({ mod, onPin }: { mod: Module; onPin: () => void }) {
  const { t } = useTranslation();
  const [iconFail, setIconFail] = useState(false);
  const widget = mod.ui?.widget;
  const kindLabel = widget?.kind === 'template' && widget.template
    ? widget.template
    : 'custom';
  const sizeLabel = widget?.size || '1x1';
  const isRunning = mod.status === 'RUNNING';

  return (
    <button
      onClick={onPin}
      style={{
        textAlign: 'left',
        background: 'var(--sf2)',
        border: '1px solid var(--b)',
        borderRadius: 10,
        padding: 10,
        cursor: 'pointer',
        display: 'flex', flexDirection: 'column', gap: 6,
        transition: 'border-color .12s, background .12s',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {mod.ui?.icon && !iconFail ? (
          <img
            src={`/api/ui/modules/${mod.name}/icon`}
            alt={mod.name}
            onError={() => setIconFail(true)}
            style={{ width: 22, height: 22, objectFit: 'contain', flexShrink: 0 }}
          />
        ) : (
          <div style={{
            width: 22, height: 22, borderRadius: 6, background: 'var(--sf3)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 11, color: 'var(--tx3)', flexShrink: 0,
          }}>
            {(mod.name || '?')[0].toUpperCase()}
          </div>
        )}
        <span style={{
          fontSize: 11, fontWeight: 500, color: 'var(--tx)',
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {mod.name}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        <span aria-hidden style={{
          width: 5, height: 5, borderRadius: '50%', flexShrink: 0,
          background: isRunning ? 'var(--gr)' : 'var(--tx3)',
        }} />
        <span style={{ fontSize: 9, color: 'var(--tx3)' }}>
          {kindLabel} · {sizeLabel}
        </span>
      </div>
      <div style={{
        marginTop: 2,
        background: 'var(--ac)', color: 'var(--on-accent)',
        borderRadius: 6, padding: '4px 0',
        textAlign: 'center', fontSize: 10, fontWeight: 600,
      }}>
        + {t('dashboard.addBtn')}
      </div>
    </button>
  );
}
