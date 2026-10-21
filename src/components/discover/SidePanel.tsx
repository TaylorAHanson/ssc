import { useEffect, useRef, useState, type ReactNode } from 'react';

interface SidePanelProps {
  onClose: () => void;
  label: string;
  children: ReactNode;
}

const WIDTH_STORAGE_KEY = 'discover.sidePanel.width';
/** Matches the previous fixed `max-w-4xl`. */
const DEFAULT_WIDTH = 896;
const MIN_WIDTH = 480;
/** Always leave this much of the page visible to the left of the panel. */
const VIEWPORT_GUTTER = 64;
/** Below Tailwind's `sm` breakpoint the panel stays full width and can't be resized. */
const RESIZABLE_MIN_VIEWPORT = 640;
const KEY_STEP = 32;

function readSavedWidth(): number | null {
  if (typeof window === 'undefined') return null;
  try {
    const saved = Number(window.localStorage.getItem(WIDTH_STORAGE_KEY));
    return Number.isFinite(saved) && saved >= MIN_WIDTH ? saved : null;
  } catch {
    return null;
  }
}

function saveWidth(width: number | null) {
  try {
    if (width === null) window.localStorage.removeItem(WIDTH_STORAGE_KEY);
    else window.localStorage.setItem(WIDTH_STORAGE_KEY, String(Math.round(width)));
  } catch {
    /* ignore */
  }
}

function clampWidth(width: number, viewport: number): number {
  const max = Math.max(MIN_WIDTH, viewport - VIEWPORT_GUTTER);
  return Math.min(Math.max(width, MIN_WIDTH), max);
}

/**
 * Right-hand slide-over used for every Discover detail view (metric views,
 * tables, dashboards…) so the list behind it keeps its place. Closes on Esc
 * or a click on the backdrop. On `sm`+ screens the left edge is a drag handle
 * (also arrow keys when focused, double-click to reset); the chosen width is
 * shared by every panel and persisted in localStorage.
 */
export function SidePanel({ onClose, label, children }: SidePanelProps) {
  // null = the default width (CSS `max-w-4xl`); a number = a user-chosen width.
  const [customWidth, setCustomWidth] = useState<number | null>(readSavedWidth);
  const [viewport, setViewport] = useState(() => (typeof window === 'undefined' ? DEFAULT_WIDTH : window.innerWidth));
  const [dragging, setDragging] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const resizingRef = useRef(false);
  const backdropPressRef = useRef(false);

  const resizable = viewport >= RESIZABLE_MIN_VIEWPORT;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    const onResize = () => setViewport(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const currentWidth = () => panelRef.current?.getBoundingClientRect().width ?? Math.min(DEFAULT_WIDTH, viewport);

  const startResize = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    resizingRef.current = true;
    setDragging(true);
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
    let dragged: number | null = null;
    const onMove = (ev: PointerEvent) => {
      if (!resizingRef.current) return;
      dragged = clampWidth(window.innerWidth - ev.clientX, window.innerWidth);
      setCustomWidth(dragged);
    };
    const onUp = () => {
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
      setDragging(false);
      if (dragged !== null) saveWidth(dragged);
      // Keep the flag up until the click that ends this drag has been dispatched,
      // so releasing over the backdrop doesn't count as "click outside to close".
      window.setTimeout(() => {
        resizingRef.current = false;
      }, 0);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  };

  const resetWidth = () => {
    setCustomWidth(null);
    saveWidth(null);
  };

  const onHandleKeyDown = (e: React.KeyboardEvent) => {
    let next: number | null;
    if (e.key === 'ArrowLeft') next = currentWidth() + KEY_STEP;
    else if (e.key === 'ArrowRight') next = currentWidth() - KEY_STEP;
    else if (e.key === 'Home') next = Math.max(MIN_WIDTH, viewport - VIEWPORT_GUTTER);
    else if (e.key === 'End') next = MIN_WIDTH;
    else if (e.key === 'Enter') {
      e.preventDefault();
      resetWidth();
      return;
    } else return;
    e.preventDefault();
    const clamped = clampWidth(next, viewport);
    setCustomWidth(clamped);
    saveWidth(clamped);
  };

  const width = resizable && customWidth !== null ? clampWidth(customWidth, viewport) : null;
  const maxWidth = Math.max(MIN_WIDTH, viewport - VIEWPORT_GUTTER);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={label}>
      <div
        className="absolute inset-0 bg-slate-900/30 animate-in fade-in duration-200"
        onPointerDown={() => {
          backdropPressRef.current = !resizingRef.current;
        }}
        onClick={() => {
          const pressedHere = backdropPressRef.current;
          backdropPressRef.current = false;
          if (pressedHere && !resizingRef.current) onClose();
        }}
      />
      <div
        ref={panelRef}
        className={`relative h-full w-full shadow-2xl transition-none animate-in slide-in-from-right duration-200 ${width === null ? 'max-w-4xl' : ''}`}
        style={width === null ? undefined : { width }}
      >
        {resizable && (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize panel"
            aria-valuemin={MIN_WIDTH}
            aria-valuemax={maxWidth}
            aria-valuenow={Math.round(width ?? Math.min(DEFAULT_WIDTH, viewport))}
            tabIndex={0}
            title="Drag to resize · double-click to reset"
            onPointerDown={startResize}
            onDoubleClick={resetWidth}
            onKeyDown={onHandleKeyDown}
            className="group absolute inset-y-0 -left-1.5 z-10 flex w-3 cursor-col-resize touch-none items-center justify-center outline-none"
          >
            <div
              className={`h-full w-px transition-colors ${
                dragging ? 'bg-accent' : 'bg-transparent group-hover:bg-accent group-focus-visible:bg-accent'
              }`}
            />
            <div
              className={`absolute h-10 w-1.5 rounded-full transition-opacity ${
                dragging ? 'bg-accent opacity-100' : 'bg-slate-400 opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100'
              }`}
            />
          </div>
        )}
        <div className="h-full overflow-y-auto bg-slate-50 p-4 sm:p-6">{children}</div>
      </div>
    </div>
  );
}
