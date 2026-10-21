import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { cn } from '../../../lib/utils';

const GAP = 4;
const MARGIN = 12;
const MIN_BELOW = 320;

/** Below the button, scrolling within the space left; above it when there's little room below and more above. */
function placement(anchor: DOMRect): { top?: number; bottom?: number; maxHeight: number } {
  const below = window.innerHeight - anchor.bottom - GAP - MARGIN;
  const above = anchor.top - GAP - MARGIN;
  if (below < MIN_BELOW && above > below) return { bottom: window.innerHeight - anchor.top + GAP, maxHeight: above };
  return { top: anchor.bottom + GAP, maxHeight: below };
}

/** A button that opens a small panel below it; closes on outside click or Escape. */
export function Popover({ label, children, align = 'left', className, buttonClassName, ariaLabel }: {
  label: ReactNode;
  children: ReactNode | ((close: () => void) => ReactNode);
  align?: 'left' | 'right';
  className?: string;
  buttonClassName?: string;
  ariaLabel?: string;
}) {
  // Fixed to the viewport, so a scrolling table or dialog around the button doesn't clip the panel.
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const open = !!anchor;
  const ref = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const id = useId();
  const close = () => setAnchor(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setAnchor(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setAnchor(null);
      }
    };
    const follow = () => button.current && setAnchor(button.current.getBoundingClientRect());
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('scroll', follow, true);
    window.addEventListener('resize', follow);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('scroll', follow, true);
      window.removeEventListener('resize', follow);
    };
  }, [open]);

  return (
    <div ref={ref} className="inline-block">
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls={id}
        aria-label={ariaLabel}
        onClick={(e) => setAnchor(open ? null : e.currentTarget.getBoundingClientRect())}
        className={cn(
          'inline-flex h-9 items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 text-sm text-gray-700 hover:bg-gray-50',
          buttonClassName,
        )}
      >
        {label}
      </button>
      {anchor && (
        <div
          id={id}
          style={{ ...placement(anchor), ...(align === 'right' ? { right: window.innerWidth - anchor.right } : { left: anchor.left }) }}
          className={cn('fixed z-[60] min-w-56 overflow-y-auto rounded-lg border border-gray-200 bg-white p-1 shadow-lg', className)}
        >
          {typeof children === 'function' ? children(close) : children}
        </div>
      )}
    </div>
  );
}

export function MenuItem({ onClick, children, hint }: { onClick: () => void; children: ReactNode; hint?: string }) {
  return (
    <button type="button" onClick={onClick} className="block w-full rounded-md px-3 py-2 text-left text-sm text-gray-800 hover:bg-gray-100">
      {children}
      {hint && <span className="mt-0.5 block text-xs text-gray-500">{hint}</span>}
    </button>
  );
}
