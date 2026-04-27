import { useCallback, useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { useStore } from '../store/useStore';

/** Resolved drop target — either a sibling widget (swap) or an empty slot
 *  (move). The dataset attribute on each cell encodes the kind so a single
 *  ``elementsFromPoint`` lookup can return either. */
type DropTarget = { kind: 'widget'; name: string } | { kind: 'empty'; slot: number };

/** State + handlers for V2 dashboard edit mode. Keeps the pointer logic
 *  out of DashboardV2 so the visual component stays declarative.
 *
 *  Drag-and-drop: pointer-down on a widget cell starts a drag; the cell
 *  underneath the cursor at pointer-up is the swap target. Reorders
 *  `widgetLayout.pinned[]` via the existing `swapWidgets` action so V1 and
 *  V2 share the same persisted layout schema.
 *
 *  Resize: pointer-down on the corner handle starts a resize; cell-width
 *  and cell-height are passed in so we can convert pixel deltas to grid-
 *  cell spans. Persists via the existing `setWidgetSpan` action.
 *
 *  This is intentionally simpler than V1 Dashboard.tsx — no multi-screen
 *  carousel, no slot-based positions. The bento `auto-flow: dense` layout
 *  derives placement from the order of `pinned`, so reordering = moving. */
export interface UseBentoEditOpts {
  cellWidth: number;
  cellHeight: number;
  /** Enable drag/resize handlers. When false the hook returns no-op
   *  callbacks so DashboardV2 can render the same JSX in view mode. */
  editMode: boolean;
  /** Hard cap on a single widget's horizontal span — prevents accidental
   *  full-row stretches on small screens. */
  maxSpanCols?: number;
  maxSpanRows?: number;
}

export interface UseBentoEditResult {
  dragName: string | null;
  /** Raw drop-target string ("widget-<name>" | "empty-<slot>"). Components
   *  compare against their own data-drop attribute to render highlight. */
  dropTarget: string | null;
  onPointerDown: (name: string) => (e: ReactPointerEvent) => void;
  onResizeHandleDown: (name: string) => (e: ReactPointerEvent) => void;
}

interface DragState {
  type: 'drag';
  name: string;
  startX: number;
  startY: number;
  hasMoved: boolean;
}

interface ResizeState {
  type: 'resize';
  name: string;
  startX: number;
  startY: number;
  startCols: number;
  startRows: number;
}

type Active = DragState | ResizeState | null;

const DRAG_THRESHOLD_PX = 4;

export function useBentoEdit(opts: UseBentoEditOpts): UseBentoEditResult {
  const { cellWidth, cellHeight, editMode, maxSpanCols = 6, maxSpanRows = 4 } = opts;
  const swapWidgets = useStore((s) => s.swapWidgets);
  const moveWidgetToSlot = useStore((s) => s.moveWidgetToSlot);
  const setWidgetSpan = useStore((s) => s.setWidgetSpan);
  const widgetLayout = useStore((s) => s.widgetLayout);

  const [dragName, setDragName] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const activeRef = useRef<Active>(null);

  /** Resolve cell directly under the pointer. Returns either a sibling
   *  widget (swap target) or an empty slot (move target), encoded as a
   *  raw ``data-drop`` string for the component to highlight. */
  const dropTargetAt = useCallback(
    (clientX: number, clientY: number): { raw: string; target: DropTarget } | null => {
      const elements = document.elementsFromPoint(clientX, clientY);
      for (const el of elements) {
        const drop = (el as HTMLElement).dataset?.drop;
        if (!drop) continue;
        if (drop.startsWith('widget-')) {
          return { raw: drop, target: { kind: 'widget', name: drop.slice('widget-'.length) } };
        }
        if (drop.startsWith('empty-')) {
          const slot = parseInt(drop.slice('empty-'.length), 10);
          if (!Number.isNaN(slot)) return { raw: drop, target: { kind: 'empty', slot } };
        }
      }
      return null;
    },
    [],
  );

  // Single document-level move/up handler — installed on demand to avoid
  // touching pointer events when not editing.
  useEffect(() => {
    if (!editMode) return;

    function onMove(e: PointerEvent) {
      const a = activeRef.current;
      if (!a) return;
      if (a.type === 'drag') {
        const dx = e.clientX - a.startX;
        const dy = e.clientY - a.startY;
        if (!a.hasMoved && Math.hypot(dx, dy) > DRAG_THRESHOLD_PX) {
          a.hasMoved = true;
          setDragName(a.name);
        }
        if (a.hasMoved) {
          const hit = dropTargetAt(e.clientX, e.clientY);
          if (hit && (hit.target.kind === 'empty' || hit.target.name !== a.name)) {
            setDropTarget(hit.raw);
          } else {
            setDropTarget(null);
          }
        }
      } else if (a.type === 'resize' && cellWidth > 0 && cellHeight > 0) {
        const dCols = Math.round((e.clientX - a.startX) / (cellWidth + 10));
        const dRows = Math.round((e.clientY - a.startY) / (cellHeight + 10));
        const cols = clamp(a.startCols + dCols, 1, maxSpanCols);
        const rows = clamp(a.startRows + dRows, 1, maxSpanRows);
        setWidgetSpan(a.name, cols, rows);
      }
    }

    function onUp(e: PointerEvent) {
      const a = activeRef.current;
      if (!a) return;
      if (a.type === 'drag' && a.hasMoved) {
        const hit = dropTargetAt(e.clientX, e.clientY);
        if (hit) {
          if (hit.target.kind === 'widget' && hit.target.name !== a.name) {
            swapWidgets(a.name, hit.target.name);
          } else if (hit.target.kind === 'empty') {
            moveWidgetToSlot(a.name, hit.target.slot);
          }
        }
      }
      activeRef.current = null;
      setDragName(null);
      setDropTarget(null);
    }

    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onUp);
    return () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointercancel', onUp);
    };
  }, [editMode, cellWidth, cellHeight, maxSpanCols, maxSpanRows, dropTargetAt, swapWidgets, moveWidgetToSlot, setWidgetSpan]);

  const onPointerDown = useCallback(
    (name: string) =>
      (e: ReactPointerEvent) => {
        if (!editMode) return;
        if (e.button !== undefined && e.button !== 0) return;
        e.preventDefault();
        activeRef.current = {
          type: 'drag',
          name,
          startX: e.clientX,
          startY: e.clientY,
          hasMoved: false,
        };
      },
    [editMode],
  );

  const onResizeHandleDown = useCallback(
    (name: string) =>
      (e: ReactPointerEvent) => {
        if (!editMode) return;
        if (e.button !== undefined && e.button !== 0) return;
        const span = widgetLayout.spans[name] ?? { cols: 1, rows: 1 };
        e.preventDefault();
        e.stopPropagation();
        activeRef.current = {
          type: 'resize',
          name,
          startX: e.clientX,
          startY: e.clientY,
          startCols: span.cols,
          startRows: span.rows,
        };
      },
    [editMode, widgetLayout.spans],
  );

  return { dragName, dropTarget, onPointerDown, onResizeHandleDown };
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(Math.max(n, lo), hi);
}
