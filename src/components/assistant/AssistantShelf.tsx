import { useEffect, useRef, useState } from 'react';
import { Sparkles, X } from 'lucide-react';

interface AssistantShelfProps {
  /** Whether the shelf is open. Controlled by the parent. */
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  /** Title shown in the shelf header. */
  title: string;
  /** localStorage key used to persist the shelf width across sessions. */
  widthStorageKey: string;
  /** Initial width (px) when nothing is persisted. */
  defaultWidth?: number;
  /** Optional hint row rendered under the header. */
  subtitle?: React.ReactNode;
  /** Optional extra buttons in the header (rendered left of the close button). */
  headerActions?: React.ReactNode;
  /** Label for the floating launcher button (defaults to "Assistant"). */
  launcherLabel?: string;
  /** The shelf body — typically a <ChatView />. */
  children: React.ReactNode;
}

const MIN_WIDTH = 360;

/**
 * A full-height, right-anchored overlay "assistant" shelf with a navy floating
 * launcher. Shared by the Workflow Studio and Skills pages so every in-page
 * agent assistant looks and behaves identically (full-screen overlay, resizable,
 * click-outside to close, same launcher). Pass the page's <ChatView> as children.
 */
export function AssistantShelf({
  open,
  onOpen,
  onClose,
  title,
  widthStorageKey,
  defaultWidth = 440,
  subtitle,
  headerActions,
  launcherLabel = 'Assistant',
  children,
}: AssistantShelfProps) {
  const [width, setWidth] = useState<number>(() => {
    if (typeof window === 'undefined') return defaultWidth;
    const saved = Number(window.localStorage.getItem(widthStorageKey));
    return saved >= MIN_WIDTH ? saved : defaultWidth;
  });
  const resizingRef = useRef(false);
  const asideRef = useRef<HTMLElement | null>(null);
  // Mirror the latest width so the drag-end handler persists the current value
  // (its closure would otherwise capture a stale width from mousedown time).
  const widthRef = useRef(width);
  widthRef.current = width;

  // Drag the panel's left edge to resize. It's an overlay shelf, so widening it
  // simply covers more of the page underneath (no content reflow).
  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    resizingRef.current = true;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'ew-resize';
    const onMove = (ev: MouseEvent) => {
      if (!resizingRef.current) return;
      const max = Math.min(960, window.innerWidth - 80);
      const next = Math.min(Math.max(window.innerWidth - ev.clientX, MIN_WIDTH), max);
      setWidth(next);
    };
    const onUp = () => {
      resizingRef.current = false;
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      try {
        window.localStorage.setItem(widthStorageKey, String(widthRef.current));
      } catch {
        /* ignore */
      }
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  // Collapse when the user clicks outside the shelf (but not while dragging the
  // resize handle). The shelf is an overlay, so a click on the page underneath
  // means "I'm done with the assistant".
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (resizingRef.current) return;
      const el = asideRef.current;
      if (el && !el.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open, onClose]);

  return (
    <>
      {open && (
        <aside
          ref={asideRef}
          className="fixed top-0 right-0 bottom-0 z-40 w-full bg-white border-l border-gray-200 shadow-2xl flex flex-col"
          style={{ width, maxWidth: '95vw' }}
        >
          {/* Drag handle on the left edge to resize the shelf. */}
          <div
            onMouseDown={startResize}
            title="Drag to resize"
            className="absolute left-0 top-0 bottom-0 w-1.5 -ml-0.5 cursor-ew-resize group z-10"
          >
            <div className="h-full w-px mx-auto bg-transparent group-hover:bg-accent/60 transition-colors" />
          </div>
          <header className="flex items-center justify-between px-4 py-3 border-b border-gray-200 shrink-0">
            <div className="flex items-center gap-2 text-sm font-semibold text-heading">
              <Sparkles className="w-4 h-4 text-accent" />
              {title}
            </div>
            <div className="flex items-center gap-1">
              {headerActions}
              <button
                type="button"
                onClick={onClose}
                title="Close assistant"
                className="text-gray-400 hover:text-gray-700 p-1"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </header>
          {subtitle && (
            <div className="px-4 py-2 border-b border-gray-100 text-[11px] text-gray-500 shrink-0">
              {subtitle}
            </div>
          )}
          <div className="flex-1 min-h-0 p-3">{children}</div>
        </aside>
      )}

      {/* Floating launcher — dark navy to match the sidebar. Hidden while the
          shelf is open (the panel has its own close control). */}
      {!open && (
        <button
          type="button"
          onClick={onOpen}
          title={`Open the ${launcherLabel.toLowerCase()}`}
          className="fixed bottom-6 right-6 z-30 flex items-center gap-2 rounded-full bg-nav-bg text-nav-text pl-4 pr-5 py-3 shadow-lg hover:bg-nav-hover transition-colors"
        >
          <Sparkles className="w-5 h-5" />
          <span className="text-sm font-semibold">{launcherLabel}</span>
        </button>
      )}
    </>
  );
}
