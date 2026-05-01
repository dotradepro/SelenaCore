import { useCallback, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import type { Module } from '../../store/useStore';
import WidgetChrome from './WidgetChrome';
import { getTemplate } from './templates/registry';
import { injectTokensIntoIframe, normalizeWidgetMessage } from '../../lib/widgetMessages';
import type { PointerEvent as ReactPointerEvent } from 'react';

/** Renders a single widget. Phase 1: always iframe (legacy `widget.html`).
 *  Phase 2 will route `kind: "template"` manifests to the template registry
 *  inside this same chrome — the iframe path stays as the `kind: "custom"`
 *  fallback. */
export interface WidgetFrameProps {
  mod: Module;
  spanCols: number;
  spanRows: number;
  /** Px per row (cell height) — drives the iframe `zoom` so widget content
   *  scales with cell size. Mirrors the baseline math in the V1 WidgetShell. */
  cellHeight: number;
  editMode: boolean;
  col?: number;
  row?: number;
  isDragging?: boolean;
  isDropOver?: boolean;
  /** Active room tab — passed through to template components so they can
   *  scope their rendered items (e.g. toggle-list filters by device location). */
  activeRoom?: string;
  onUnpin?: () => void;
  onPointerDown?: (e: ReactPointerEvent) => void;
  onResizeHandleDown?: (e: ReactPointerEvent) => void;
}

const BASELINE_CELL_PX = 90;

export default function WidgetFrame(props: WidgetFrameProps) {
  const {
    mod, spanCols, spanRows, cellHeight, editMode,
    col, row, isDragging, isDropOver, activeRoom,
    onUnpin, onPointerDown, onResizeHandleDown,
  } = props;

  const { t } = useTranslation();
  const navigate = useNavigate();
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const isRunning = mod.status === 'RUNNING';
  const contentScale = cellHeight > 0 ? cellHeight / BASELINE_CELL_PX : 1;

  // Phase 2 — route templates through the registry. Falls back to the
  // iframe path below if the manifest declares an unknown template name.
  const isTemplate = mod.ui?.widget?.kind === 'template';
  const TemplateComponent = isTemplate ? getTemplate(mod.ui?.widget?.template) : null;

  // Inject CSS zoom + grid span info AND design tokens into the iframe so
  // widgets adapt to their cell size and pick up the parent's theme tokens
  // without shipping their own copy of `index.css`. Mirrors V1
  // Dashboard.tsx WidgetShell, plus the Phase 4 token injection.
  const injectScale = useCallback(() => {
    const iframe = iframeRef.current;
    if (!iframe) return;
    try {
      const doc = iframe.contentDocument;
      if (!doc?.head) return;
      doc.getElementById('__selena_scale')?.remove();
      const s = doc.createElement('style');
      s.id = '__selena_scale';
      s.textContent = `html { zoom: ${contentScale.toFixed(3)} !important; }`;
      doc.head.appendChild(s);
      doc.documentElement.dataset.spanCols = String(spanCols);
      doc.documentElement.dataset.spanRows = String(spanRows);
    } catch { /* cross-origin or not yet loaded */ }
    injectTokensIntoIframe(iframe);
  }, [contentScale, spanCols, spanRows]);

  useEffect(() => { injectScale(); }, [injectScale]);

  // Phase 4 — typed postMessage protocol (with deprecation aliases).
  useEffect(() => {
    function handleMsg(e: MessageEvent) {
      const msg = normalizeWidgetMessage(e.data);
      if (!msg) return;
      switch (msg.type) {
        case 'open_settings':
          if (msg.module === mod.name) navigate(`/modules/${mod.name}`);
          break;
        // modal_open / modal_close / modal_resize stay handled by the
        // top-level Dashboard handler so the modal lives in a single place.
        default:
          break;
      }
    }
    window.addEventListener('message', handleMsg);
    return () => window.removeEventListener('message', handleMsg);
  }, [mod.name, navigate]);

  // Debug-style hover label (`<name> · :<port>`) is only useful for legacy
  // iframe widgets — they don't render their own header. Template widgets
  // already show a localized title inside their own JSX, so the overlay
  // would just duplicate text and add visual noise. Suppress it there.
  const chromeLabel = isTemplate && TemplateComponent
    ? undefined
    : `${mod.name} · :${mod.port}`;

  return (
    <WidgetChrome
      name={mod.name}
      status={mod.status}
      col={col}
      row={row}
      spanCols={spanCols}
      spanRows={spanRows}
      editMode={editMode}
      isDragging={isDragging}
      isDropOver={isDropOver}
      onUnpin={onUnpin}
      onPointerDown={onPointerDown}
      onResizeHandleDown={onResizeHandleDown}
      label={chromeLabel}
    >
      {isRunning && TemplateComponent ? (
        <div style={{ width: '100%', height: '100%', pointerEvents: editMode ? 'none' : 'auto' }}>
          <TemplateComponent mod={mod} activeRoom={activeRoom} />
        </div>
      ) : isRunning ? (
        <iframe
          ref={iframeRef}
          src={`/api/ui/modules/${mod.name}/widget`}
          scrolling="no"
          title={mod.name}
          onLoad={injectScale}
          style={{
            width: '100%', height: '100%', border: 'none', display: 'block',
            pointerEvents: editMode ? 'none' : 'auto',
          }}
        />
      ) : (
        <StoppedPlaceholder
          name={mod.name}
          editMode={editMode}
          onClick={() => !editMode && navigate(`/modules/${mod.name}`)}
          stoppedLabel={t('dashboard.widgetStopped') as string}
        />
      )}
    </WidgetChrome>
  );
}

function StoppedPlaceholder({
  name, editMode, onClick, stoppedLabel,
}: { name: string; editMode: boolean; onClick: () => void; stoppedLabel: string }) {
  const initial = (name || '?')[0].toUpperCase();
  return (
    <div
      onClick={onClick}
      style={{
        width: '100%', height: '100%',
        display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center',
        gap: 5, opacity: 0.4, cursor: editMode ? 'default' : 'pointer',
      }}
    >
      <div style={{
        width: 32, height: 32, borderRadius: 9, background: 'var(--sf2)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 14, fontWeight: 500, color: 'var(--tx3)',
      }}>{initial}</div>
      <div style={{ fontSize: 9, fontWeight: 500, textTransform: 'uppercase', letterSpacing: '.05em', color: 'var(--tx3)' }}>
        {name}
      </div>
      <div style={{ fontSize: 9, color: 'var(--tx3)' }}>{stoppedLabel}</div>
    </div>
  );
}
