import type { ReactNode, PointerEvent as ReactPointerEvent } from 'react';

/** Module status as expressed by the loader. We only differentiate three
 *  states visually — running, error, anything-else (stopped/uploaded/...) —
 *  to keep the dot palette aligned with V1. */
type ModStatus = 'RUNNING' | 'ERROR' | string;

export interface WidgetChromeProps {
  name: string;
  status: ModStatus;
  /** CSS Grid placement — col/row are 1-indexed when explicit; pass undefined
   *  to let the parent BentoGrid auto-flow place the cell. */
  col?: number;
  row?: number;
  spanCols: number;
  spanRows: number;
  editMode: boolean;
  isDragging?: boolean;
  isDropOver?: boolean;
  onUnpin?: () => void;
  onPointerDown?: (e: ReactPointerEvent) => void;
  onResizeHandleDown?: (e: ReactPointerEvent) => void;
  /** Optional footer label (V1 shows `<name> · :<port>`). */
  label?: string;
  children: ReactNode;
}

export default function WidgetChrome({
  name,
  status,
  col,
  row,
  spanCols,
  spanRows,
  editMode,
  isDragging,
  isDropOver,
  onUnpin,
  onPointerDown,
  onResizeHandleDown,
  label,
  children,
}: WidgetChromeProps) {
  const isRunning = status === 'RUNNING';
  const isErr = status === 'ERROR';
  const dotCls = isRunning ? 'wsd-run' : isErr ? 'wsd-err' : 'wsd-stop';

  const gridStyle: { gridColumn?: string; gridRow?: string } = {};
  if (col !== undefined) gridStyle.gridColumn = `${col} / span ${spanCols}`;
  else gridStyle.gridColumn = `span ${spanCols}`;
  if (row !== undefined) gridStyle.gridRow = `${row} / span ${spanRows}`;
  else gridStyle.gridRow = `span ${spanRows}`;

  return (
    <div
      className={[
        'ws',
        editMode ? 'edit-mode' : '',
        isDragging ? 'ws-dragging' : '',
        isDropOver ? 'drag-over' : '',
      ].filter(Boolean).join(' ')}
      data-drop={`widget-${name}`}
      onPointerDown={editMode ? onPointerDown : undefined}
      style={{
        touchAction: editMode ? 'none' : 'auto',
        ...gridStyle,
      }}
    >
      <div className={`ws-dot ${dotCls}`} />

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

      {children}

      {label && <div className="ws-label">{label}</div>}
    </div>
  );
}
