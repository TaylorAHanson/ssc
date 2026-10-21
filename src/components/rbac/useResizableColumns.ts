import { useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent, type PointerEvent } from 'react';

const STORAGE_PREFIX = 'rbac:col-widths:';
const MIN_WIDTH = 48;
const MAX_WIDTH = 1200;
const FALLBACK_WIDTH = 120;
const KEY_STEP = 16;

type Widths = Record<string, number>;

function readWidths(key: string): Widths {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(STORAGE_PREFIX + key) ?? '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).filter((e): e is [string, number] => typeof e[1] === 'number' && Number.isFinite(e[1]) && e[1] > 0),
    );
  } catch {
    return {};
  }
}

function writeWidths(key: string, widths: Widths) {
  try {
    if (Object.keys(widths).length) window.localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(widths));
    else window.localStorage.removeItem(STORAGE_PREFIX + key);
  } catch {
    // Storage can be disabled or full; the widths then last until the page reloads.
  }
}

/** Widths of the header cells marked `data-col` in the table's first header row. */
function measure(table: HTMLTableElement | null): Widths {
  const out: Widths = {};
  for (const cell of Array.from(table?.tHead?.rows[0]?.cells ?? [])) {
    const id = cell.dataset.col;
    // Rounding down by a fraction of a pixel is enough to put an ellipsis on text that just fitted.
    if (id) out[id] = Math.ceil(cell.getBoundingClientRect().width);
  }
  return out;
}

const clamp = (w: number) => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(w)));

function releaseBody() {
  document.body.style.removeProperty('cursor');
  document.body.style.removeProperty('user-select');
}

export interface ColumnHandleProps {
  onPointerDown: (e: PointerEvent<HTMLElement>) => void;
  onPointerMove: (e: PointerEvent<HTMLElement>) => void;
  onPointerUp: (e: PointerEvent<HTMLElement>) => void;
  onPointerCancel: (e: PointerEvent<HTMLElement>) => void;
  onMouseDown: (e: MouseEvent<HTMLElement>) => void;
  onClick: (e: MouseEvent<HTMLElement>) => void;
  onDoubleClick: (e: MouseEvent<HTMLElement>) => void;
  onKeyDown: (e: KeyboardEvent<HTMLElement>) => void;
  'aria-valuenow': number;
  'aria-valuemin': number;
  'aria-valuemax': number;
}

export interface ColumnWidths {
  /** Every column, in order, resizable or not. */
  ids: string[];
  /** Some column has a width of its own, so the table uses a fixed layout. */
  active: boolean;
  /** Some column has a saved width, so there's something to reset. */
  customized: boolean;
  tableStyle: CSSProperties | undefined;
  widthOf: (id: string) => number;
  handleProps: (id: string) => ColumnHandleProps;
  resetColumn: (id: string) => void;
  resetAll: () => void;
}

export interface ResizableColumns extends ColumnWidths {
  /** The `<table>`'s ref; the cells of its first header row carry `data-col={id}`. */
  attachTable: (node: HTMLTableElement | null) => void;
}

/**
 * Drag-to-resize columns for a plain `<table>`, remembered per `storageKey` in localStorage.
 * Until a column is resized the table keeps its automatic layout; the first resize freezes every
 * column at its current width and switches to a fixed layout. Resetting the last custom width
 * goes back to the automatic layout.
 *
 * Destructure `attachTable` away from the rest: the React Compiler lint treats the whole object
 * as a ref once any part of it is passed as one.
 */
export function useResizableColumns(storageKey: string, ids: string[]): ResizableColumns {
  const [stored, setStored] = useState(() => ({ key: storageKey, widths: readWidths(storageKey) }));
  if (stored.key !== storageKey) setStored({ key: storageKey, widths: readWidths(storageKey) });

  const signature = ids.join('\u0000');
  const [natural, setNatural] = useState<{ signature: string; widths: Widths } | null>(null);
  const table = useRef<HTMLTableElement | null>(null);
  const drag = useRef<{ id: string; startX: number; startWidth: number } | null>(null);

  // The table first renders with the automatic layout, so this records each column's natural width.
  const attachTable = useCallback(
    (node: HTMLTableElement | null) => {
      table.current = node;
      if (node) setNatural({ signature, widths: measure(node) });
    },
    [signature],
  );

  useEffect(() => writeWidths(stored.key, stored.widths), [stored]);
  useEffect(() => () => {
    if (drag.current) releaseBody();
  }, []);

  const custom: Widths = {};
  for (const id of ids) if (stored.widths[id]) custom[id] = stored.widths[id];
  const customized = Object.keys(custom).length > 0;
  const naturalWidths = natural?.signature === signature ? natural.widths : null;
  const active = customized && naturalWidths !== null;
  const widthOf = (id: string) => custom[id] ?? naturalWidths?.[id] ?? FALLBACK_WIDTH;

  const setWidth = (id: string, width: number) =>
    setStored((prev) => (prev.widths[id] === width ? prev : { key: prev.key, widths: { ...prev.widths, [id]: width } }));

  const resetColumn = (id: string) =>
    setStored((prev) => {
      const widths = { ...prev.widths };
      delete widths[id];
      return { key: prev.key, widths };
    });

  /** A column's current width; on the first resize it freezes the automatic layout's widths. */
  const currentWidth = (id: string) => {
    if (active) return widthOf(id);
    const measured = measure(table.current);
    setNatural({ signature, widths: measured });
    return measured[id] ?? FALLBACK_WIDTH;
  };

  const endDrag = (e: PointerEvent<HTMLElement>) => {
    if (!drag.current) return;
    drag.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    releaseBody();
  };

  const handleProps = (id: string): ColumnHandleProps => ({
    onPointerDown: (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      drag.current = { id, startX: e.clientX, startWidth: currentWidth(id) };
      e.currentTarget.setPointerCapture(e.pointerId);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    },
    onPointerMove: (e) => {
      const d = drag.current;
      if (d?.id === id) setWidth(id, clamp(d.startWidth + e.clientX - d.startX));
    },
    onPointerUp: endDrag,
    onPointerCancel: endDrag,
    // Keeps focus where it is, so grabbing a handle doesn't close an open cell editor.
    onMouseDown: (e) => {
      e.preventDefault();
      e.stopPropagation();
    },
    onClick: (e) => e.stopPropagation(),
    onDoubleClick: (e) => {
      e.stopPropagation();
      resetColumn(id);
    },
    onKeyDown: (e) => {
      const delta = e.key === 'ArrowLeft' ? -KEY_STEP : e.key === 'ArrowRight' ? KEY_STEP : 0;
      if (delta) {
        e.preventDefault();
        setWidth(id, clamp(currentWidth(id) + delta));
      } else if (e.key === 'Enter' || e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        resetColumn(id);
      }
    },
    'aria-valuenow': widthOf(id),
    'aria-valuemin': MIN_WIDTH,
    'aria-valuemax': MAX_WIDTH,
  });

  return {
    attachTable,
    ids,
    active,
    customized,
    tableStyle: active ? { tableLayout: 'fixed', width: ids.reduce((sum, id) => sum + widthOf(id), 0) } : undefined,
    widthOf,
    handleProps,
    resetColumn,
    resetAll: () => setStored((prev) => ({ key: prev.key, widths: {} })),
  };
}
