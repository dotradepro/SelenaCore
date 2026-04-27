import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useStore, type Module } from '../../store/useStore';
import { useElevated } from '../../hooks/useElevated';
import PinConfirmModal from '../PinConfirmModal';
import Hero from './Hero';
import SceneRow from './SceneRow';
import RoomTabs, { ALL_ROOM, moduleMatchesRoom } from './RoomTabs';
import BentoGrid from './BentoGrid';
import WidgetFrame from './WidgetFrame';
import AddWidgetDrawer from './AddWidgetDrawer';
import { useBentoEdit } from '../../hooks/useBentoEdit';

/** Parse a manifest WxH string. Returns null on missing/malformed input so
 *  the caller can fall back to a sensible default. */
function parseSize(s?: string): { cols: number; rows: number } | null {
  if (!s) return null;
  const m = s.match(/^(\d+)x(\d+)$/);
  return m ? { cols: parseInt(m[1], 10), rows: parseInt(m[2], 10) } : null;
}

interface SecurityFlags {
  edit_mode_pin: boolean;
  device_toggle_pin: boolean;
  kiosk_mode: boolean;
}

const DEFAULT_SECURITY: SecurityFlags = {
  edit_mode_pin: true,
  device_toggle_pin: false,
  kiosk_mode: false,
};

export default function DashboardV2() {
  const { t } = useTranslation();
  const modules = useStore((s) => s.modules);
  const fetchModules = useStore((s) => s.fetchModules);
  const widgetLayout = useStore((s) => s.widgetLayout);
  const unpinModule = useStore((s) => s.unpinModule);
  const resetWidgetLayout = useStore((s) => s.resetWidgetLayout);

  useEffect(() => { fetchModules(); }, [fetchModules]);

  const [editMode, setEditMode] = useState(false);
  const [showAddDrawer, setShowAddDrawer] = useState(false);
  const [activeRoom, setActiveRoom] = useState<string>(ALL_ROOM);
  const [cellSize, setCellSize] = useState({ cellWidth: 0, cellHeight: 0, cols: 4 });

  // PIN-gate edit mode (mirrors V1 behavior — same /api/ui/security endpoint).
  const { requestElevation, pinModalProps } = useElevated();
  const [security, setSecurity] = useState<SecurityFlags>(DEFAULT_SECURITY);
  useEffect(() => {
    fetch('/api/ui/security')
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => { if (s) setSecurity(s); })
      .catch(() => { /* leave defaults */ });
  }, []);

  const toggleEditMode = useCallback(() => {
    if (editMode) {
      setEditMode(false);
      setShowAddDrawer(false);
      return;
    }
    if (security.kiosk_mode || security.edit_mode_pin) {
      requestElevation(() => setEditMode(true));
      return;
    }
    setEditMode(true);
  }, [editMode, security, requestElevation]);

  const { dragName, dropTarget, onPointerDown, onResizeHandleDown } = useBentoEdit({
    cellWidth: cellSize.cellWidth,
    cellHeight: cellSize.cellHeight,
    editMode,
    maxSpanCols: cellSize.cols,
  });

  // Resolve the ordered list of pinned widgets, filtered by the active room tab.
  const pinnedMods: Module[] = widgetLayout.pinned
    .map((name) => modules.find((m) => m.name === name))
    .filter((m): m is Module => Boolean(m))
    .filter((m) => moduleMatchesRoom(m, activeRoom));

  function spanFor(m: Module): { cols: number; rows: number } {
    const saved = widgetLayout.spans?.[m.name];
    if (saved) return saved;
    const fromManifest = parseSize(m.ui?.widget?.size);
    return fromManifest ?? { cols: 1, rows: 1 };
  }

  // Pre-compute spans so BentoGrid can size cells to fit every widget.
  const widgetSpans = pinnedMods.map(spanFor);

  // Phone breakpoint = scrollable column; everything else = clamped to
  // viewport. We track the live width so the outer container can switch
  // between `overflow: hidden` (kiosk) and `overflow-y: auto` (phone).
  const [vw, setVw] = useState<number>(typeof window === 'undefined' ? 1440 : window.innerWidth);
  useEffect(() => {
    function onResize() { setVw(window.innerWidth); }
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  const isPhone = vw < 480;

  return (
    <div
      style={{
        position: 'absolute', inset: 0,
        padding: '10px 12px 14px',
        display: 'flex', flexDirection: 'column',
        overflowY: isPhone ? 'auto' : 'hidden',
      }}
    >
      <Hero />
      <SceneRow />
      <RoomTabs modules={modules} active={activeRoom} onChange={setActiveRoom} />

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 4px 8px', flexShrink: 0 }}>
        <div style={{ fontSize: 11, color: 'var(--tx3)' }}>
          {t('dashboardV2.label.widgetCount', { count: pinnedMods.length })}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {editMode && (
            <button
              onClick={() => setShowAddDrawer(true)}
              title={t('dashboard.addWidget') as string}
              style={{
                fontSize: 10, padding: '4px 10px', borderRadius: 8,
                background: 'var(--ac)', color: 'var(--on-accent)', border: '1px solid var(--ac)',
                cursor: 'pointer', fontWeight: 600,
              }}
            >
              + {t('dashboard.addWidget')}
            </button>
          )}
          {editMode && (
            <button
              onClick={() => {
                if (window.confirm(t('dashboardV2.label.resetConfirm') as string)) {
                  resetWidgetLayout();
                }
              }}
              title={t('dashboardV2.label.resetTitle') as string}
              style={{
                fontSize: 10, padding: '4px 10px', borderRadius: 8,
                background: 'transparent', color: 'var(--rd)', border: '1px solid var(--rd)',
                cursor: 'pointer',
              }}
            >
              {t('dashboardV2.label.reset')}
            </button>
          )}
          <button
            onClick={toggleEditMode}
            style={{
              fontSize: 10, padding: '4px 10px', borderRadius: 8,
              background: editMode ? 'var(--ac)' : 'var(--sf)',
              color: editMode ? 'var(--on-accent)' : 'var(--tx2)',
              border: `1px solid ${editMode ? 'var(--ac)' : 'var(--b)'}`,
              cursor: 'pointer',
            }}
          >
            {editMode ? t('dashboard.done') : t('dashboard.edit')}
          </button>
        </div>
      </div>

      <div
        style={{
          flex: '1 1 auto', minHeight: 0, position: 'relative',
          // Visible 5×4 grid guidelines in edit mode — soft dotted pattern
          // sized to the measured cellWidth/cellHeight so users can see
          // exactly which cell their widget will land in.
          backgroundImage: editMode && cellSize.cellHeight > 0
            ? 'linear-gradient(to right, var(--b) 1px, transparent 1px), linear-gradient(to bottom, var(--b) 1px, transparent 1px)'
            : 'none',
          backgroundSize: editMode
            ? `${cellSize.cellWidth + 10}px ${cellSize.cellHeight + 10}px`
            : 'auto',
          backgroundPosition: '0 0',
        }}
      >
        <BentoGrid widgetSpans={widgetSpans} onCellSize={setCellSize}>
        {/* V1-style positioning: each widget anchored at its `positions[name]`
            slot (0..GRID_COLS*GRID_ROWS-1). Empty cells render as drop-target
            placeholders in edit mode so the user can drop onto any free slot
            instead of just swapping with another widget. */}
        {(() => {
          const GRID_COLS = cellSize.cols || 5;
          const GRID_ROWS = 4;
          const TOTAL = GRID_COLS * GRID_ROWS;
          const positions = widgetLayout.positions ?? {};

          // Map slot → module for pinned widgets, ignoring covered cells.
          const slotMap = new Map<number, Module>();
          pinnedMods.forEach((mod) => {
            const slot = positions[mod.name];
            if (slot !== undefined && slot < TOTAL) slotMap.set(slot, mod);
          });

          // Cells covered by multi-cell widget spans (anchor + interior).
          const covered = new Set<number>();
          slotMap.forEach((mod, anchorSlot) => {
            const span = widgetLayout.spans?.[mod.name] ?? parseSize(mod.ui?.widget?.size) ?? { cols: 1, rows: 1 };
            const anchorCol = anchorSlot % GRID_COLS;
            const anchorRow = Math.floor(anchorSlot / GRID_COLS);
            const spanCols = Math.min(span.cols, GRID_COLS - anchorCol);
            const spanRows = Math.min(span.rows, GRID_ROWS - anchorRow);
            for (let r = 0; r < spanRows; r++) {
              for (let c = 0; c < spanCols; c++) {
                if (r === 0 && c === 0) continue;
                covered.add((anchorRow + r) * GRID_COLS + (anchorCol + c));
              }
            }
          });

          const cells: JSX.Element[] = [];
          for (let slot = 0; slot < TOTAL; slot++) {
            const col = (slot % GRID_COLS) + 1;
            const row = Math.floor(slot / GRID_COLS) + 1;
            const mod = slotMap.get(slot);
            if (mod) {
              const span = widgetLayout.spans?.[mod.name] ?? parseSize(mod.ui?.widget?.size) ?? { cols: 1, rows: 1 };
              cells.push(
                <WidgetFrame
                  key={`w-${mod.name}`}
                  mod={mod}
                  col={col}
                  row={row}
                  spanCols={Math.min(span.cols, GRID_COLS - col + 1)}
                  spanRows={Math.min(span.rows, GRID_ROWS - row + 1)}
                  cellHeight={cellSize.cellHeight}
                  editMode={editMode}
                  isDragging={dragName === mod.name}
                  isDropOver={dropTarget === `widget-${mod.name}` || dropTarget === mod.name}
                  onUnpin={() => unpinModule(mod.name)}
                  onPointerDown={onPointerDown(mod.name)}
                  onResizeHandleDown={onResizeHandleDown(mod.name)}
                />
              );
            } else if (editMode && !covered.has(slot)) {
              const isDropOver = dropTarget === `empty-${slot}`;
              cells.push(
                <div
                  key={`e-${slot}`}
                  className={`ws ws-empty${isDropOver ? ' drag-over' : ''}`}
                  data-drop={`empty-${slot}`}
                  style={{
                    gridColumn: `${col} / span 1`,
                    gridRow: `${row} / span 1`,
                  }}
                >
                  <span style={{ fontSize: 16, lineHeight: 1, pointerEvents: 'none' }}>+</span>
                </div>
              );
            }
          }
          return cells;
        })()}
        </BentoGrid>
      </div>

      {pinnedMods.length === 0 && (
        <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--tx3)', fontSize: 12 }}>
          {t('dashboardV2.label.empty')}
          <div style={{ marginTop: 12 }}>
            <button
              onClick={() => {
                if (!editMode) {
                  toggleEditMode();
                }
                setShowAddDrawer(true);
              }}
              style={{
                fontSize: 11, padding: '6px 14px', borderRadius: 8,
                background: 'var(--ac)', color: 'var(--on-accent)',
                border: '1px solid var(--ac)', cursor: 'pointer', fontWeight: 600,
              }}
            >
              + {t('dashboard.addWidget')}
            </button>
          </div>
        </div>
      )}

      {showAddDrawer && (
        <AddWidgetDrawer
          modules={modules}
          pinned={widgetLayout.pinned}
          onClose={() => setShowAddDrawer(false)}
        />
      )}

      <PinConfirmModal {...pinModalProps} />
    </div>
  );
}
