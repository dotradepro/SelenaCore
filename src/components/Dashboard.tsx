import { useEffect, useLayoutEffect, useState, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import type React from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useStore, Module } from '../store/useStore';

// Fixed grid: always 5 columns × 4 rows — cells scale to fill any screen
const GRID_COLS = 5;
const GRID_ROWS = 4;
const PER_SCREEN = GRID_COLS * GRID_ROWS; // 20 slots
const GRID_GAP = 8;
const GRID_PAD = 10;  // padding on each screen div side
const TOOLBAR_H = 40;  // space reserved for the Edit toolbar

/* ── Widget Shell ── */
function WidgetShell({
  mod, editMode, col, row, spanCols, spanRows, contentScale, isDragging, isDropOver,
  onUnpin, onPointerDown, onResizeHandleDown,
}: {
  mod: Module;
  editMode: boolean;
  col: number;
  row: number;
  spanCols: number;
  spanRows: number;
  contentScale: number;
  isDragging?: boolean;
  isDropOver?: boolean;
  onUnpin?: () => void;
  onPointerDown?: (e: React.PointerEvent) => void;
  onResizeHandleDown?: (e: React.PointerEvent) => void;
}) {
  const navigate = useNavigate();
  const isRunning = mod.status === 'RUNNING';
  const isErr = mod.status === 'ERROR';
  const dotCls = isRunning ? 'wsd-run' : isErr ? 'wsd-err' : 'wsd-stop';
  const initial = (mod.name || '?')[0].toUpperCase();
  const iframeRef = useRef<HTMLIFrameElement>(null);

  // Inject CSS zoom + grid span info into iframe content so fonts/layout
  // scale with cell size and widgets can adapt to their actual cell count.
  const injectScale = useCallback(() => {
    try {
      const doc = iframeRef.current?.contentDocument;
      if (!doc?.head) return;
      doc.getElementById('__selena_scale')?.remove();
      const s = doc.createElement('style');
      s.id = '__selena_scale';
      s.textContent = `html { zoom: ${contentScale.toFixed(3)} !important; }`;
      doc.head.appendChild(s);
      // Push grid span so widgets can switch layout based on real cell count
      // (aspect ratio is unreliable because grid cells aren't square).
      doc.documentElement.dataset.spanCols = String(spanCols);
      doc.documentElement.dataset.spanRows = String(spanRows);
    } catch { /* cross-origin or not yet loaded */ }
  }, [contentScale, spanCols, spanRows]);

  // Re-inject whenever scale or grid span changes
  useEffect(() => { injectScale(); }, [injectScale]);

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
    <div
      className={[
        'ws',
        editMode ? 'edit-mode' : '',
        isDragging ? 'ws-dragging' : '',
        isDropOver ? 'drag-over' : '',
      ].filter(Boolean).join(' ')}
      data-drop={`widget-${mod.name}`}
      onPointerDown={editMode ? onPointerDown : undefined}
      style={{
        touchAction: editMode ? 'none' : 'auto',
        gridColumn: `${col} / span ${spanCols}`,
        gridRow: `${row} / span ${spanRows}`,
      }}
    >
      <div className={`ws-dot ${dotCls}`} />

      {/* Edit mode control bar */}
      {editMode && (
        <div className="ws-edit-bar">
          <button
            className="ws-eb-btn ws-eb-red"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); onUnpin?.(); }}
            title="Remove"
          >×</button>
        </div>
      )}

      {/* Corner resize handle — drag to resize widget */}
      {editMode && (
        <div
          className="ws-resize-handle"
          onPointerDown={(e) => {
            e.stopPropagation();
            e.preventDefault();
            onResizeHandleDown?.(e);
          }}
        />
      )}

      {isRunning ? (
        <iframe
          ref={iframeRef}
          src={`/api/ui/modules/${mod.name}/widget`}
          scrolling="no"
          title={mod.name}
          onLoad={injectScale}
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

/* ── Empty Slot — drop target visible in edit mode ── */
function EmptySlot({ globalIdx, col, row, isDropOver }: { globalIdx: number; col: number; row: number; isDropOver: boolean }) {
  return (
    <div
      className={`ws ws-empty${isDropOver ? ' drag-over' : ''}`}
      data-drop={`empty-${globalIdx}`}
      style={{ gridColumn: `${col} / span 1`, gridRow: `${row} / span 1` }}
    >
      <span style={{ fontSize: 16, lineHeight: 1, pointerEvents: 'none' }}>+</span>
    </div>
  );
}

/* ── Add Widget Drawer ── */
function WidgetPreview({ mod }: { mod: Module }) {
  const [iconFail, setIconFail] = useState(false);
  const TYPE_EMOJI: Record<string, string> = {
    INTEGRATION: '🔗', DRIVER: '🔌', SYSTEM: '⚙️',
    AUTOMATION: '⚡', UI: '🖥', IMPORT_SOURCE: '📥',
  };

  if (mod.status === 'RUNNING' && mod.ui?.widget?.file) {
    return (
      <div style={{
        width: '100%', height: 80, overflow: 'hidden',
        borderRadius: 8, position: 'relative', background: 'var(--sf2)',
        pointerEvents: 'none',
      }}>
        {/* scale trick: 30% of original widget */}
        <iframe
          src={`/api/ui/modules/${mod.name}/widget`}
          scrolling="no"
          title={`${mod.name} preview`}
          style={{
            position: 'absolute', top: 0, left: 0,
            width: '333%', height: '333%',
            transform: 'scale(0.3)', transformOrigin: 'top left',
            border: 'none', pointerEvents: 'none',
          }}
        />
      </div>
    );
  }

  return (
    <div style={{
      width: '100%', height: 80,
      background: 'var(--sf2)', borderRadius: 8,
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', gap: 4,
    }}>
      {mod.ui?.icon && !iconFail ? (
        <img
          src={`/api/ui/modules/${mod.name}/icon`}
          alt={mod.name}
          onError={() => setIconFail(true)}
          style={{ width: 28, height: 28, objectFit: 'contain' }}
        />
      ) : (
        <span style={{ fontSize: 22 }}>{TYPE_EMOJI[mod.type] || '📦'}</span>
      )}
      <span style={{ fontSize: 9, color: 'var(--tx3)', opacity: .7 }}>
        {mod.status.toLowerCase()}
      </span>
    </div>
  );
}

function AddWidgetDrawer({
  modules,
  pinned,
  onPin,
  onClose,
}: {
  modules: Module[];
  pinned: string[];
  onPin: (name: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();

  // All modules with a widget file that are not currently pinned
  const available = modules.filter(
    (m) => !!m.ui?.widget?.file && !pinned.includes(m.name)
  );

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'absolute', inset: 0, zIndex: 40,
          background: 'rgba(0,0,0,.45)',
        }}
      />
      {/* Drawer */}
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0,
        zIndex: 50,
        background: 'var(--sf)',
        borderRadius: '14px 14px 0 0',
        padding: '12px 14px 20px',
        boxShadow: '0 -4px 24px rgba(0,0,0,.3)',
      }}>
        {/* Handle */}
        <div style={{
          width: 36, height: 4, borderRadius: 2,
          background: 'var(--sf3)', margin: '0 auto 12px',
        }} />

        <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--tx)', marginBottom: 12 }}>
          {t('dashboard.addWidget', 'Add widget')}
        </div>

        {available.length === 0 ? (
          <div style={{
            textAlign: 'center', padding: '20px 0',
            color: 'var(--tx3)', fontSize: 11,
          }}>
            {t('dashboard.allPinned', 'All available widgets are already on the dashboard.')}
          </div>
        ) : (
          <div style={{
            display: 'flex', gap: 10,
            overflowX: 'auto', paddingBottom: 4,
            scrollbarWidth: 'none',
          }}>
            {available.map((mod) => (
              <div
                key={mod.name}
                style={{
                  flexShrink: 0, width: 110,
                  background: 'var(--sf2)',
                  borderRadius: 10,
                  padding: 8,
                  cursor: 'pointer',
                  border: '1px solid var(--b)',
                  transition: 'border-color .15s',
                }}
                onClick={() => { onPin(mod.name); onClose(); }}
              >
                <WidgetPreview mod={mod} />
                <div style={{ marginTop: 6 }}>
                  <div style={{
                    fontSize: 10, fontWeight: 600, color: 'var(--tx)',
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}>
                    {mod.name}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2 }}>
                    <div style={{
                      width: 5, height: 5, borderRadius: '50%', flexShrink: 0,
                      background: mod.status === 'RUNNING' ? 'var(--gr)' : 'var(--tx3)',
                    }} />
                    <span style={{ fontSize: 9, color: 'var(--tx3)' }}>{mod.type}</span>
                  </div>
                </div>
                <div style={{
                  marginTop: 8, width: '100%',
                  background: 'var(--ac)', color: '#fff',
                  borderRadius: 6, padding: '4px 0',
                  textAlign: 'center', fontSize: 10, fontWeight: 600,
                }}>
                  + {t('dashboard.addBtn', 'Add')}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

/* ── Dashboard (Widget Homescreen) ── */
export default function Dashboard() {
  const { t } = useTranslation();
  const modules = useStore((s) => s.modules);
  const fetchModules = useStore((s) => s.fetchModules);
  const widgetLayout = useStore((s) => s.widgetLayout);
  const pinModule = useStore((s) => s.pinModule);
  const unpinModule = useStore((s) => s.unpinModule);
  const setWidgetSize = useStore((s) => s.setWidgetSize);
  const swapWidgets = useStore((s) => s.swapWidgets);
  const moveWidgetToSlot = useStore((s) => s.moveWidgetToSlot);
  const setWidgetSpan = useStore((s) => s.setWidgetSpan);
  const addScreen = useStore((s) => s.addScreen);

  const [currentScreen, setCurrentScreen] = useState(0);
  const [editMode, setEditMode] = useState(false);
  const [showAddDrawer, setShowAddDrawer] = useState(false);
  const [modalMod, setModalMod] = useState<string | null>(null);
  // Optional content-driven modal size, set by widgets that send a
  // `modal_resize` postMessage right after their first render.
  const [modalSize, setModalSize] = useState<{ width?: number; height?: number }>({});

  // ── Widget fullscreen modal (openWidgetModal / closeWidgetModal) ─────────
  useEffect(() => {
    function handleWidgetModal(e: MessageEvent) {
      if (!e.data) return;
      if (e.data.type === 'openWidgetModal' && typeof e.data.module === 'string') {
        // Widget can pass an initial size hint to avoid the "open big →
        // resize small" flash. modal_resize messages from inside the
        // iframe still override this later if the content needs more
        // space than the hint allowed.
        const w0 = typeof e.data.width === 'number' ? e.data.width : undefined;
        const h0 = typeof e.data.height === 'number' ? e.data.height : undefined;
        setModalSize(w0 || h0 ? { width: w0, height: h0 } : {});
        setModalMod(e.data.module);
      }
      if (e.data.type === 'closeWidgetModal') {
        // Notify the widget iframe to refresh its state immediately
        const mod = e.data.module || modalMod;
        if (mod) {
          document.querySelectorAll<HTMLIFrameElement>('iframe').forEach(f => {
            if (f.src.includes(`/modules/${mod}/widget`) && !f.src.includes('modal=1')) {
              try { f.contentWindow?.postMessage({ type: 'refresh' }, '*'); } catch (_) { }
            }
          });
        }
        setModalMod(null);
        setModalSize({});
      }
      if (e.data.type === 'modal_resize') {
        const w = typeof e.data.width === 'number' ? e.data.width : undefined;
        const h = typeof e.data.height === 'number' ? e.data.height : undefined;
        if (w || h) setModalSize({ width: w, height: h });
      }
    }
    window.addEventListener('message', handleWidgetModal);
    return () => window.removeEventListener('message', handleWidgetModal);
  }, []);

  // ── Screen size — measured from container ────────────────────────────────
  const containerRef = useRef<HTMLDivElement>(null);
  const [screenW, setScreenW] = useState(0);
  const [cellW, setCellW] = useState(0);
  const [cellH, setCellH] = useState(0);

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      const { width, height } = el.getBoundingClientRect();
      if (!width || !height) return;
      const usableW = width - GRID_PAD * 2;
      const usableH = height - GRID_PAD * 2 - TOOLBAR_H;
      setScreenW(width);
      setCellW((usableW - GRID_GAP * (GRID_COLS - 1)) / GRID_COLS);
      setCellH((usableH - GRID_GAP * (GRID_ROWS - 1)) / GRID_ROWS);
    };
    measure();
    const obs = new ResizeObserver(measure);
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // Use measured width or sensible fallback until first measurement
  const SCREEN_W = screenW || 748;

  // Content scale: ratio of actual cell height to baseline 90px.
  // Applied as CSS zoom inside each widget iframe so fonts/layout grow with cell size.
  const contentScale = cellH > 0 ? Math.round((cellH / 80) * 100) / 100 : 1;

  // ── Drag state ──────────────────────────────────────────────────────────────
  const [dragName, setDragName] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null); // 'widget-NAME' or 'empty-N'
  const [ghostPos, setGhostPos] = useState({ x: 0, y: 0 });
  // swipeStart: tracks a finger/pointer that may turn into a screen swipe
  const swipeStart = useRef<{ x: number; y: number } | null>(null);
  // swipeLocked: true while dragging/resizing — disables swipe
  const swipeLocked = useRef(false);
  const edgeScrollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // ── Resize state ─────────────────────────────────────────────────────────────────
  const [resizeState, setResizeState] = useState<{
    name: string;
    startX: number; startY: number;
    startCols: number; startRows: number;
    currentCols: number; currentRows: number;
  } | null>(null);
  // Pinned modules in display order
  const pinnedMods = widgetLayout.pinned
    .map(name => modules.find(m => m.name === name))
    .filter(Boolean) as Module[];

  // Any module with a widget that is NOT currently pinned (for the add drawer)
  const unpinnedWidgetMods = modules.filter(
    (m) => !!m.ui?.widget?.file && !widgetLayout.pinned.includes(m.name)
  );

  // Build slot map from positions (absolute slot index -> Module)
  const slotMap = new Map<number, Module>();
  pinnedMods.forEach(mod => {
    const pos = (widgetLayout.positions ?? {})[mod.name];
    if (pos !== undefined) slotMap.set(pos, mod);
  });

  /** Parse manifest size string "2x2" to {cols, rows} */
  function parseSize(s?: string): { cols: number; rows: number } | null {
    if (!s) return null;
    const m = s.match(/^(\d+)x(\d+)$/);
    return m ? { cols: parseInt(m[1], 10), rows: parseInt(m[2], 10) } : null;
  }

  /** Returns max grid span from manifest max_size (or grid limits) */
  function getMaxSpan(mod: Module): { cols: number; rows: number } {
    return parseSize(mod.ui?.widget?.max_size) || { cols: GRID_COLS, rows: GRID_ROWS };
  }

  /** Returns min grid span from manifest min_size (or 1×1) */
  function getMinSpan(mod: Module): { cols: number; rows: number } {
    return parseSize(mod.ui?.widget?.min_size) || { cols: 1, rows: 1 };
  }

  /** Returns the saved grid span; defaults to manifest size (or 1×1).
   *  Clamped by manifest min_size / max_size so manifest changes take effect
   *  even when the user already has a saved span in their store. */
  function getWidgetSpan(mod: Module): { cols: number; rows: number } {
    const custom = (widgetLayout.spans ?? {})[mod.name];
    const raw = custom || parseSize(mod.ui?.widget?.size) || { cols: 1, rows: 1 };
    const min = getMinSpan(mod);
    const max = getMaxSpan(mod);
    return {
      cols: Math.min(Math.max(raw.cols, min.cols), max.cols),
      rows: Math.min(Math.max(raw.rows, min.rows), max.rows),
    };
  }

  // Compute which slots are covered by non-anchor cells of multi-cell widgets.
  // Spans are clamped to the current grid size — a span saved on a wide-screen PC
  // must not overflow the grid on a narrower device.
  const coveredSlots = new Set<number>();
  slotMap.forEach((mod, slotIdx) => {
    const rawSpan = resizeState?.name === mod.name
      ? { cols: resizeState.currentCols, rows: resizeState.currentRows }
      : getWidgetSpan(mod);
    if (rawSpan.cols === 1 && rawSpan.rows === 1) return;
    const localIdx = slotIdx % PER_SCREEN;
    const screenIdx = Math.floor(slotIdx / PER_SCREEN);
    const anchorCol = localIdx % GRID_COLS;
    const anchorRow = Math.floor(localIdx / GRID_COLS);
    // Clamp so we never reach past the edge of the grid on any screen size
    const spanCols = Math.min(rawSpan.cols, GRID_COLS - anchorCol);
    const spanRows = Math.min(rawSpan.rows, GRID_ROWS - anchorRow);
    for (let r = 0; r < spanRows; r++) {
      for (let c = 0; c < spanCols; c++) {
        if (r === 0 && c === 0) continue; // anchor itself
        const li = (anchorRow + r) * GRID_COLS + (anchorCol + c);
        if (li < PER_SCREEN) coveredSlots.add(screenIdx * PER_SCREEN + li);
      }
    }
  });

  // Total screens = max(user minimum, screens needed to show all positioned widgets)
  const maxOccupiedSlot = pinnedMods.reduce((mx, m) => {
    const pos = (widgetLayout.positions ?? {})[m.name];
    return pos !== undefined ? Math.max(mx, pos) : mx;
  }, -1);
  const screensByPosition = maxOccupiedSlot >= 0 ? Math.ceil((maxOccupiedSlot + 1) / PER_SCREEN) : 1;
  const totalScreens = Math.max(
    widgetLayout.screens ?? 1,
    screensByPosition
  );

  // Circular: left swipe from screen 0 → last screen, right swipe from last → screen 0
  const go = (dir: number) =>
    setCurrentScreen((s) => (s + dir + totalScreens) % totalScreens);

  // ── Drag handlers ────────────────────────────────────────────────────────────
  function startDrag(name: string, e: React.PointerEvent) {
    e.preventDefault();
    e.stopPropagation();
    // Capture pointer so move/up events always reach the container
    const container = containerRef.current;
    if (container) container.setPointerCapture(e.pointerId);
    setDragName(name);
    setGhostPos({ x: e.clientX, y: e.clientY });
    setDropTarget(null);
    swipeLocked.current = true;
  }

  function startResize(name: string, e: React.PointerEvent, cols: number, rows: number) {
    e.preventDefault();
    const container = containerRef.current;
    if (container) container.setPointerCapture(e.pointerId);
    setResizeState({ name, startX: e.clientX, startY: e.clientY, startCols: cols, startRows: rows, currentCols: cols, currentRows: rows });
    swipeLocked.current = true;
  }

  function handlePointerMove(e: React.PointerEvent) {
    if (dragName) {
      setGhostPos({ x: e.clientX, y: e.clientY });

      // Find element under pointer (ghost has pointer-events:none so it's ignored)
      const el = document.elementFromPoint(e.clientX, e.clientY);
      const target = el?.closest('[data-drop]') as HTMLElement | null;
      setDropTarget(target?.dataset.drop ?? null);

      // Auto-scroll to adjacent screen when dragging near left/right edges
      const rect = containerRef.current?.getBoundingClientRect();
      if (rect) {
        const EDGE = 55;
        const relX = e.clientX - rect.left;
        if (edgeScrollTimer.current) {
          clearTimeout(edgeScrollTimer.current);
          edgeScrollTimer.current = null;
        }
        if (relX < EDGE && currentScreen > 0) {
          edgeScrollTimer.current = setTimeout(() => go(-1), 500);
        } else if (rect.width - relX < EDGE && currentScreen < totalScreens - 1) {
          edgeScrollTimer.current = setTimeout(() => go(1), 500);
        }
      }
    } else if (resizeState && cellW > 0 && cellH > 0) {
      const deltaCols = Math.round((e.clientX - resizeState.startX) / (cellW + GRID_GAP));
      const deltaRows = Math.round((e.clientY - resizeState.startY) / (cellH + GRID_GAP));
      // Clamp span to grid bounds and manifest min_size / max_size
      const anchorSlot = (widgetLayout.positions ?? {})[resizeState.name];
      const localIdx = anchorSlot !== undefined ? anchorSlot % PER_SCREEN : 0;
      const anchorCol = localIdx % GRID_COLS;
      const anchorRow = Math.floor(localIdx / GRID_COLS);
      const resizeMod = modules.find(m => m.name === resizeState.name);
      const maxSpan = resizeMod ? getMaxSpan(resizeMod) : { cols: GRID_COLS, rows: GRID_ROWS };
      const minSpan = resizeMod ? getMinSpan(resizeMod) : { cols: 1, rows: 1 };
      const newCols = Math.max(minSpan.cols, Math.min(GRID_COLS - anchorCol, maxSpan.cols, resizeState.startCols + deltaCols));
      const newRows = Math.max(minSpan.rows, Math.min(GRID_ROWS - anchorRow, maxSpan.rows, resizeState.startRows + deltaRows));
      if (newCols !== resizeState.currentCols || newRows !== resizeState.currentRows) {
        setResizeState(s => s ? { ...s, currentCols: newCols, currentRows: newRows } : null);
      }
    } else if (swipeStart.current) {
      // Prevent browser swipe-back/forward when horizontal movement dominates
      const dx = e.clientX - swipeStart.current.x;
      const dy = e.clientY - swipeStart.current.y;
      if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 8) {
        e.preventDefault();
      }
    }
  }

  function handlePointerUp(e?: React.PointerEvent) {
    if (dragName && dropTarget) {
      if (dropTarget.startsWith('widget-')) {
        const dstName = dropTarget.slice(7);
        if (dstName !== dragName) swapWidgets(dragName, dstName);
      } else if (dropTarget.startsWith('empty-')) {
        const dstIdx = parseInt(dropTarget.slice(6), 10);
        if (!isNaN(dstIdx)) moveWidgetToSlot(dragName, dstIdx);
      }
    }
    if (resizeState) {
      setWidgetSpan(resizeState.name, resizeState.currentCols, resizeState.currentRows);
      setResizeState(null);
    }
    if (swipeStart.current && e) {
      const dx = e.clientX - swipeStart.current.x;
      const dy = e.clientY - swipeStart.current.y;
      if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 40) {
        go(dx < 0 ? 1 : -1);
      }
      swipeStart.current = null;
    }
    if (edgeScrollTimer.current) { clearTimeout(edgeScrollTimer.current); edgeScrollTimer.current = null; }
    setDragName(null);
    setDropTarget(null);
    swipeLocked.current = false;
  }

  function handleContainerPointerDown(e: React.PointerEvent) {
    // Only track swipe when not dragging/resizing/editing
    if (swipeLocked.current || editMode) return;
    // Only start swipe tracking on direct container taps (not widget buttons)
    const target = e.target as HTMLElement;
    if (target.closest('.ws') || target.closest('.edit-toggle')) return;
    swipeStart.current = { x: e.clientX, y: e.clientY };
  }

  return (
    <div
      ref={containerRef}
      style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden', touchAction: 'none' }}
      onPointerDown={handleContainerPointerDown}
      onPointerMove={dragName || resizeState || swipeStart.current ? handlePointerMove : undefined}
      onPointerUp={dragName || resizeState || swipeStart.current ? handlePointerUp : undefined}
      onPointerCancel={dragName || resizeState || swipeStart.current ? handlePointerUp : undefined}
    >
      {/* ── Toolbar ── */}
      <div style={{ position: 'absolute', top: 6, right: 6, zIndex: 30, display: 'flex', gap: 6, alignItems: 'center' }}>
        {editMode && unpinnedWidgetMods.length > 0 && (
          <div className="edit-toggle" onClick={() => setShowAddDrawer(v => !v)}>
            + {t('dashboard.addWidget', 'Add')} ({unpinnedWidgetMods.length})
          </div>
        )}
        {editMode && (
          <div className="edit-toggle" onClick={addScreen} title={t('dashboard.addScreen', 'Add screen')}>
            ⊕ {t('dashboard.addScreen', 'Screen')}
          </div>
        )}
        <div
          className={`edit-toggle${editMode ? ' on' : ''}`}
          onClick={() => { setEditMode(v => !v); setShowAddDrawer(false); }}
        >
          {editMode ? t('dashboard.done', 'Done') : t('dashboard.edit', 'Edit')}
        </div>
      </div>

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

      {/* ── Screens track ── */}
      <div style={{
        display: 'flex',
        height: '100%',
        transition: dragName ? 'none' : 'transform .35s cubic-bezier(.4,0,.2,1)',
        transform: `translateX(-${currentScreen * SCREEN_W}px)`,
      }}>
        {Array.from({ length: totalScreens }, (_, si) => {
          const startIdx = si * PER_SCREEN;
          // Build all PER_SCREEN slots for this screen: widget or empty
          const slots = Array.from({ length: PER_SCREEN }, (_, i) => {
            const globalIdx = startIdx + i;
            const col = (i % GRID_COLS) + 1;
            const row = Math.floor(i / GRID_COLS) + 1;
            return { mod: slotMap.get(globalIdx) ?? null, globalIdx, col, row };
          });

          return (
            <div key={si} style={{ flexShrink: 0, width: SCREEN_W, height: '100%', padding: 10 }}>
              <div
                className={editMode ? 'widget-grid widget-grid--edit' : 'widget-grid'}
                style={{
                  display: 'grid',
                  gridTemplateColumns: `repeat(${GRID_COLS}, 1fr)`,
                  gridTemplateRows: `repeat(${GRID_ROWS}, 1fr)`,
                  gap: GRID_GAP,
                  width: '100%',
                  height: `calc(100% - ${GRID_PAD * 2}px)`,
                }}
              >
                {slots.map(({ mod, globalIdx, col, row }) => {
                  // Skip empty slots that are visually covered by a multi-cell widget.
                  // Never skip a slot that has a real widget anchor — render it at 1×1 instead.
                  const isCovered = coveredSlots.has(globalIdx);
                  if (isCovered && !mod) return null;
                  if (mod) {
                    const baseSpan = getWidgetSpan(mod);
                    const rawSpan = resizeState?.name === mod.name
                      ? { cols: resizeState.currentCols, rows: resizeState.currentRows }
                      : baseSpan;
                    // Clamp to grid bounds from this anchor position;
                    // if the anchor is itself covered by another widget, force 1×1
                    const maxCols = GRID_COLS - (col - 1);
                    const maxRows = GRID_ROWS - (row - 1);
                    const wspan = isCovered ? { cols: 1, rows: 1 } : {
                      cols: Math.min(rawSpan.cols, maxCols),
                      rows: Math.min(rawSpan.rows, maxRows),
                    };
                    return (
                      <WidgetShell
                        key={mod.name}
                        mod={mod}
                        editMode={editMode}
                        col={col}
                        row={row}
                        spanCols={wspan.cols}
                        spanRows={wspan.rows}
                        contentScale={contentScale}
                        isDragging={dragName === mod.name}
                        isDropOver={dropTarget === `widget-${mod.name}` && dragName !== mod.name}
                        onUnpin={() => unpinModule(mod.name)}
                        onPointerDown={(e) => startDrag(mod.name, e)}
                        onResizeHandleDown={(e) => startResize(mod.name, e, wspan.cols, wspan.rows)}
                      />
                    );
                  }
                  // Empty slot: show dashed drop target in edit mode, nothing in view mode
                  if (editMode) {
                    return (
                      <EmptySlot
                        key={`empty-${globalIdx}`}
                        globalIdx={globalIdx}
                        col={col}
                        row={row}
                        isDropOver={dropTarget === `empty-${globalIdx}`}
                      />
                    );
                  }
                  return null;
                })}
              </div>
            </div>
          );
        })}
      </div>

      {/* ── Drag ghost ── */}
      {dragName && createPortal(
        <div style={{
          position: 'fixed',
          left: ghostPos.x,
          top: ghostPos.y,
          transform: 'translate(-50%, -50%) scale(1.12)',
          minWidth: 64,
          padding: '6px 10px',
          background: 'var(--sf2)',
          border: '2px solid var(--ac)',
          borderRadius: 10,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 10,
          fontWeight: 600,
          color: 'var(--ac)',
          zIndex: 9999,
          pointerEvents: 'none',
          boxShadow: '0 10px 32px rgba(0,0,0,.55)',
          backdropFilter: 'blur(6px)',
          letterSpacing: '0.03em',
          whiteSpace: 'nowrap',
        }}>
          {dragName.length > 14 ? dragName.slice(0, 13) + '…' : dragName}
        </div>,
        document.body
      )}

      {/* ── Add widget drawer ── */}
      {editMode && showAddDrawer && (
        <AddWidgetDrawer
          modules={modules}
          pinned={widgetLayout.pinned}
          onPin={(name) => {
            // Find first slot not occupied by any anchor AND not covered by a multi-cell span
            const allUsed = new Set<number>(slotMap.keys());
            coveredSlots.forEach(s => allUsed.add(s));
            let slot = 0;
            while (allUsed.has(slot)) slot++;
            pinModule(name, slot);
          }}
          onClose={() => setShowAddDrawer(false)}
        />
      )}

      {/* ── Widget fullscreen modal ── */}
      {modalMod && createPortal(
        (() => {
          // If the widget reported a desired size via modal_resize, honour
          // it (clamped to viewport). Otherwise fall back to the legacy
          // "almost fullscreen" panel for widgets that don't opt in.
          const hasSize = !!(modalSize.width || modalSize.height);
          const panelStyle: React.CSSProperties = hasSize
            ? {
              position: 'absolute',
              top: '50%', left: '50%',
              transform: 'translate(-50%, -50%)',
              width: modalSize.width
                ? `min(${modalSize.width}px, 92vw)`
                : 'auto',
              height: modalSize.height
                ? `min(${modalSize.height}px, 92vh)`
                : 'auto',
              maxWidth: '92vw',
              maxHeight: '92vh',
              background: 'var(--sf)',
              borderRadius: 20,
              overflow: 'hidden',
              boxShadow: '0 40px 120px rgba(0,0,0,.65)',
              display: 'flex',
              flexDirection: 'column',
              transition: 'width .18s ease-out, height .18s ease-out',
            }
            : {
              position: 'absolute',
              top: '4%', left: '5%', right: '5%', bottom: '4%',
              background: 'var(--sf)',
              borderRadius: 20,
              overflow: 'hidden',
              boxShadow: '0 40px 120px rgba(0,0,0,.65)',
              display: 'flex',
              flexDirection: 'column',
            };
          return (
            <div style={{ position: 'fixed', inset: 0, zIndex: 9990 }}>
              {/* Backdrop */}
              <div
                onClick={() => { setModalMod(null); setModalSize({}); }}
                style={{
                  position: 'absolute', inset: 0,
                  background: 'rgba(0,0,0,.72)',
                  backdropFilter: 'blur(8px)',
                  WebkitBackdropFilter: 'blur(8px)',
                }}
              />
              {/* Panel */}
              <div style={panelStyle}>
                <iframe
                  src={`/api/ui/modules/${modalMod}/widget?modal=1`}
                  scrolling="no"
                  title="Widget source picker"
                  style={{ flex: 1, border: 'none', width: '100%', height: '100%', display: 'block' }}
                />
              </div>
            </div>
          );
        })(),
        document.body
      )}

      {/* ── Page dots ── */}
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
