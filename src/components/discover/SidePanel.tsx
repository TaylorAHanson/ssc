import { useEffect, type ReactNode } from 'react';

interface SidePanelProps {
  onClose: () => void;
  label: string;
  children: ReactNode;
}

/**
 * Right-hand slide-over used for every Discover detail view (metric views,
 * tables, dashboards…) so the list behind it keeps its place. Closes on Esc
 * or a click on the backdrop.
 */
export function SidePanel({ onClose, label, children }: SidePanelProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={label}>
      <div className="absolute inset-0 bg-slate-900/30 animate-in fade-in duration-200" onClick={onClose} />
      <div className="relative h-full w-full max-w-4xl overflow-y-auto bg-slate-50 p-4 sm:p-6 shadow-2xl animate-in slide-in-from-right duration-200">
        {children}
      </div>
    </div>
  );
}
