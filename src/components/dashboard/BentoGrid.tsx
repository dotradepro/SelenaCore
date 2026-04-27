import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

/** Fixed-grid resolver. Reverted to V1's 5×4 layout per user feedback —
 *  bento auto-flow squeezed widgets into illegible heights. Phone falls
 *  back to one column with vertical scroll because 5 cols on 375px is
 *  unreadable. Tablets keep a friendlier 4 columns. */
function colsForWidth(w: number): number {
  if (w < 480) return 1;
  if (w < 900) return 4;
  return 5;
}

/** Visible rows per "page" of the grid. Mirrors V1 GRID_ROWS = 4 so the
 *  whole 5×4 = 20 cells worth of widgets fit on a 1080p kiosk in one
 *  screen. Phone breakpoint = 0 → cell aspect-ratio + scroll. */
function rowsForWidth(w: number): number {
  if (w < 480) return 0;
  return 4;
}

const GAP_PX = 10;

export interface BentoGridProps {
  children: ReactNode;
  cols?: number;
  /** Span shape ``[{cols, rows}, ...]`` of the children, in render order.
   *  Unused by the fixed 5×4 layout but kept in the prop signature so we
   *  can switch back to bento auto-flow without touching DashboardV2. */
  widgetSpans?: { cols: number; rows: number }[];
  onCellSize?: (size: { cellWidth: number; cellHeight: number; cols: number }) => void;
}

export default function BentoGrid({ children, cols: forcedCols, onCellSize }: BentoGridProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [cols, setCols] = useState<number>(forcedCols ?? 5);
  const [cellSize, setCellSize] = useState({ cellWidth: 0, cellHeight: 0 });
  const [scrollable, setScrollable] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      const width = rect.width;
      const parentH = el.parentElement?.getBoundingClientRect().height ?? 0;
      const usableH = parentH > 0 ? parentH : Math.max(0, window.innerHeight - rect.top - 14);

      const nextCols = forcedCols ?? colsForWidth(width);
      const cellWidth = (width - GAP_PX * (nextCols - 1)) / nextCols;

      const targetRows = rowsForWidth(width);
      let cellHeight: number;
      if (targetRows === 0 || usableH < 200) {
        // Phone — square-ish cells, vertical scroll.
        cellHeight = Math.max(72, Math.round(cellWidth * 0.85));
        setScrollable(true);
      } else {
        cellHeight = Math.max(72, Math.floor((usableH - GAP_PX * (targetRows - 1)) / targetRows));
        // Always allow scroll when total content height could exceed the
        // viewport — V1 did this via a multi-screen carousel; we accept
        // a vertical scroll instead until the carousel is reintroduced.
        setScrollable(false);
      }

      setCols(nextCols);
      setCellSize({ cellWidth, cellHeight });
    };
    measure();
    const obs = new ResizeObserver(measure);
    obs.observe(el);
    if (el.parentElement) obs.observe(el.parentElement);
    window.addEventListener('resize', measure);
    return () => {
      obs.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [forcedCols]);

  useEffect(() => {
    if (cellSize.cellWidth > 0) onCellSize?.({ ...cellSize, cols });
  }, [cellSize, cols, onCellSize]);

  return (
    <div
      ref={ref}
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
        gridAutoRows: `${cellSize.cellHeight || 90}px`,
        gridAutoFlow: 'dense',
        gap: `${GAP_PX}px`,
        width: '100%',
        height: scrollable ? 'auto' : '100%',
        overflowY: scrollable ? 'auto' : 'auto',
      }}
    >
      {children}
    </div>
  );
}
