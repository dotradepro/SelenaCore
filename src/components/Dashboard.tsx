import { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStore, Module } from '../store/useStore';

/* ── Widget Shell ── */
function WidgetShell({
  mod, editMode, size = 'normal', onUnpin, onResize, onMoveLeft, onMoveRight, canMoveLeft, canMoveRight,
}: {
  mod: Module;
  editMode: boolean;
  size?: 'compact' | 'normal';
  onUnpin?: () => void;
  onResize?: () => void;
  onMoveLeft?: () => void;
  onMoveRight?: () => void;
  canMoveLeft?: boolean;
  canMoveRight?: boolean;
}) {
  const navigate = useNavigate();
  const isRunning = mod.status === 'RUNNING';
  const isErr = mod.status === 'ERROR';
  const dotCls = isRunning ? 'wsd-run' : isErr ? 'wsd-err' : 'wsd-stop';
  const initial = (mod.name || '?')[0].toUpperCase();

  // Size: compact = 1x1, normal = manifest size or fallback
  const manifestSize = mod.ui?.widget?.size;
  const span = size === 'compact' ? 'sp-1x1' : (manifestSize ? `sp-${manifestSize}` : (mod.type === 'SYSTEM' ? 'sp-1x1' : 'sp-2x1'));

  // Listen for openSettings postMessage from widget iframe
  useEffect(() => {
    function handleMsg(e: MessageEvent) {
      if (e.data?.type === 'openSettings' && e.data?.module === mod.name) {
        navigate(`/modules/${mod.name}`);
      }
    }
    window.addEventListener('message', handleMsg);
    return () => window.removeEventListener('message', handleMsg);
  }, [mod.name, navigate]);

  return (
    <div className={`ws ${span}${editMode ? ' edit-mode' : ''}`}>
      <div className={`ws-dot ${dotCls}`} />
      {/* Edit mode control bar */}
      {editMode && (
        <div className="ws-edit-bar">
          <button className="ws-eb-btn ws-eb-red" onClick={onUnpin} title="Unpin">−</button>
          <button className="ws-eb-btn" onClick={onResize} title={size === 'compact' ? 'Expand' : 'Compact'}>
            {size === 'compact' ? '⊞' : '⊟'}
          </button>
          <button className="ws-eb-btn" onClick={onMoveLeft} disabled={!canMoveLeft} title="Move left">←</button>
          <button className="ws-eb-btn" onClick={onMoveRight} disabled={!canMoveRight} title="Move right">→</button>
        </div>
      )}

      {isRunning ? (
        <iframe
          src={`/api/ui/modules/${mod.name}/widget`}
          sandbox="allow-scripts allow-same-origin"
          scrolling="no"
          title={mod.name}
          style={{ width: '100%', height: '100%', border: 'none', display: 'block', pointerEvents: editMode ? 'none' : 'auto' }}
        />
      ) : (
        <div
          style={{
            width: '100%', height: '100%',
            display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center',
            gap: 5, opacity: .4, cursor: editMode ? 'default' : 'pointer',
          }}
          onClick={() => !editMode && navigate(`/modules/${mod.name}`)}
        >
          <div style={{
            width: 32, height: 32, borderRadius: 9,
            background: 'var(--sf2)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 14, fontWeight: 500, color: 'var(--tx3)',
          }}>
            {initial}
          </div>
          <div style={{ fontSize: 9, fontWeight: 500, textTransform: 'uppercase', letterSpacing: '.05em', color: 'var(--tx3)' }}>
            {mod.name}
          </div>
          <div style={{ fontSize: 9, color: 'var(--tx3)' }}>Stopped</div>
        </div>
      )}
      <div className="ws-label">{mod.name} · :{mod.port}</div>
    </div>
  );
}

/* ── Dashboard (Widget Homescreen) ── */
export default function Dashboard() {
  const modules = useStore((s) => s.modules);
  const fetchModules = useStore((s) => s.fetchModules);
  const widgetLayout = useStore((s) => s.widgetLayout);
  const pinModule = useStore((s) => s.pinModule);
  const unpinModule = useStore((s) => s.unpinModule);
  const setWidgetSize = useStore((s) => s.setWidgetSize);
  const moveWidget = useStore((s) => s.moveWidget);

  const [currentScreen, setCurrentScreen] = useState(0);
  const [editMode, setEditMode] = useState(false);
  const [showAddPanel, setShowAddPanel] = useState(false);
  const swipeRef = useRef<number | null>(null);

  useEffect(() => { fetchModules(); }, [fetchModules]);

  // Pinned modules in display order
  const pinnedMods = widgetLayout.pinned
    .map(name => modules.find(m => m.name === name))
    .filter(Boolean) as Module[];

  // Unpinned system modules with a widget (available to add to dashboard)
  const unpinnedSystemMods = modules.filter(m =>
    m.type === 'SYSTEM' && !!m.ui?.widget?.file && !widgetLayout.pinned.includes(m.name)
  );

  const PER_SCREEN = 6;
  const totalScreens = Math.max(1, Math.ceil(pinnedMods.length / PER_SCREEN));

  function getScreenMods(si: number): Array<{ mod: Module; pinIdx: number }> {
    const startIdx = si * PER_SCREEN;
    const endIdx = startIdx + PER_SCREEN;
    return pinnedMods.slice(startIdx, endIdx).map((mod, i) => ({ mod, pinIdx: startIdx + i }));
  }

  // Sidebar is 52px; total viewport 800px
  const SCREEN_W = 748;

  const go = (dir: number) =>
    setCurrentScreen((s) => Math.max(0, Math.min(totalScreens - 1, s + dir)));

  function onTouchStart(e: React.TouchEvent) { swipeRef.current = e.touches[0].clientX; }
  function onTouchEnd(e: React.TouchEvent) {
    if (swipeRef.current === null) return;
    const dx = e.changedTouches[0].clientX - swipeRef.current;
    if (Math.abs(dx) > 40) go(dx < 0 ? 1 : -1);
    swipeRef.current = null;
  }

  return (
    <div
      style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden' }}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      {/* Edit toggle */}
      <div style={{ position: 'absolute', top: 6, right: 6, zIndex: 30, display: 'flex', gap: 6, alignItems: 'center' }}>
        {editMode && unpinnedSystemMods.length > 0 && (
          <div className="edit-toggle" onClick={() => setShowAddPanel(v => !v)}>
            + Add ({unpinnedSystemMods.length})
          </div>
        )}
        <div
          className={`edit-toggle${editMode ? ' on' : ''}`}
          onClick={() => { setEditMode(v => !v); setShowAddPanel(false); }}
        >
          {editMode ? 'Done' : 'Edit'}
        </div>
      </div>

      {/* Add system module panel */}
      {editMode && showAddPanel && (
        <div className="add-mod-panel">
          <div style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.06em', color: 'var(--tx3)', marginBottom: 6 }}>
            Add to Dashboard
          </div>
          {unpinnedSystemMods.map(m => (
            <div key={m.name} className="add-mod-item" onClick={() => { pinModule(m.name); setShowAddPanel(false); }}>
              <div style={{ width: 6, height: 6, borderRadius: '50%', background: m.status === 'RUNNING' ? 'var(--gr)' : 'var(--tx3)', flexShrink: 0 }} />
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.name}</span>
              <span style={{ color: 'var(--ac)', fontSize: 14, lineHeight: 1, flexShrink: 0 }}>+</span>
            </div>
          ))}
        </div>
      )}

      {/* Left arrow */}
      {currentScreen > 0 && (
        <div className="pg-arrow left" onClick={() => go(-1)}>
          <svg viewBox="0 0 10 10" fill="none" width="12" height="12">
            <path d="M6.5 2L3.5 5l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      )}

      {/* Right arrow */}
      {currentScreen < totalScreens - 1 && (
        <div className="pg-arrow right" onClick={() => go(1)}>
          <svg viewBox="0 0 10 10" fill="none" width="12" height="12">
            <path d="M3.5 2L6.5 5l-3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      )}

      {/* Screens track */}
      <div style={{
        display: 'flex',
        height: '100%',
        transition: 'transform .35s cubic-bezier(.4,0,.2,1)',
        transform: `translateX(-${currentScreen * SCREEN_W}px)`,
      }}>
        {Array.from({ length: totalScreens }, (_, si) => {
          const mods = getScreenMods(si);
          const maxSlots = PER_SCREEN;
          return (
            <div key={si} style={{ flexShrink: 0, width: SCREEN_W, height: '100%', padding: 10 }}>
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(4, 1fr)',
                gridTemplateRows: 'repeat(3, 1fr)',
                gap: 8,
                width: '100%',
                height: 'calc(100% - 20px)',
              }}>
                {mods.map(({ mod, pinIdx }) => (
                  <WidgetShell
                    key={mod.name}
                    mod={mod}
                    editMode={editMode}
                    size={widgetLayout.sizes[mod.name] ?? 'normal'}
                    onUnpin={() => unpinModule(mod.name)}
                    onResize={() => setWidgetSize(mod.name, (widgetLayout.sizes[mod.name] ?? 'normal') === 'compact' ? 'normal' : 'compact')}
                    onMoveLeft={() => moveWidget(mod.name, -1)}
                    onMoveRight={() => moveWidget(mod.name, 1)}
                    canMoveLeft={pinIdx > 0}
                    canMoveRight={pinIdx < pinnedMods.length - 1}
                  />
                ))}

                {/* Add slot (edit mode) */}
                {editMode && mods.length < maxSlots && (
                  <div className="ws ws-empty sp-1x1">
                    <svg viewBox="0 0 16 16" fill="none" width="18" height="18" style={{ color: 'var(--tx3)' }}>
                      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.3" />
                      <path d="M8 5v6M5 8h6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                    </svg>
                    <div style={{ fontSize: 9, color: 'var(--tx3)', marginTop: 2 }}>Add</div>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Page dots */}
      {totalScreens > 1 && (
        <div style={{
          position: 'absolute', bottom: 6, left: '50%', transform: 'translateX(-50%)',
          display: 'flex', gap: 5, zIndex: 10, pointerEvents: 'none',
        }}>
          {Array.from({ length: totalScreens }, (_, i) => (
            <div
              key={i}
              style={{
                width: i === currentScreen ? 14 : 5,
                height: 5,
                borderRadius: i === currentScreen ? 3 : '50%',
                background: i === currentScreen ? 'var(--tx2)' : 'var(--tx3)',
                transition: 'all .25s',
                pointerEvents: 'auto',
                cursor: 'pointer',
              }}
              onClick={() => setCurrentScreen(i)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
