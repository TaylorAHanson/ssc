import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { AlertTriangle, Check, CheckCircle2, Copy, Info, Loader2, RefreshCw, X, XCircle } from 'lucide-react';
import { Button } from '../ui/button';
import { cn } from '../../lib/utils';

export type Tone = 'gray' | 'green' | 'amber' | 'red' | 'blue' | 'indigo';

const TONES: Record<Tone, string> = {
  gray: 'bg-gray-50 text-gray-600 border-gray-200',
  green: 'bg-green-50 text-green-800 border-green-200',
  amber: 'bg-amber-50 text-amber-800 border-amber-200',
  red: 'bg-red-50 text-red-800 border-red-200',
  blue: 'bg-blue-50 text-blue-800 border-blue-200',
  indigo: 'bg-indigo-50 text-indigo-800 border-indigo-200',
};

export function Badge({ tone = 'gray', title, className, children }: {
  tone?: Tone;
  title?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={cn('inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-xs', TONES[tone], className)}
    >
      {children}
    </span>
  );
}

const NOTICE_ICONS = { info: Info, warn: AlertTriangle, error: XCircle, success: CheckCircle2 };
const NOTICE_STYLES = {
  info: 'border-blue-200 bg-blue-50 text-blue-900',
  warn: 'border-amber-200 bg-amber-50 text-amber-900',
  error: 'border-red-200 bg-red-50 text-red-800',
  success: 'border-green-200 bg-green-50 text-green-800',
};

export function Notice({ kind = 'info', title, children, className }: {
  kind?: keyof typeof NOTICE_STYLES;
  title?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  const Icon = NOTICE_ICONS[kind];
  return (
    <div role={kind === 'error' ? 'alert' : undefined} className={cn('flex items-start gap-2 rounded-md border p-3 text-sm', NOTICE_STYLES[kind], className)}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">
        {title && <div className="font-medium">{title}</div>}
        {children && <div className={title ? 'mt-0.5' : undefined}>{children}</div>}
      </div>
    </div>
  );
}

export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-center justify-between gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
      <span className="flex min-w-0 items-start gap-2">
        <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
        <span className="min-w-0 break-words">{message}</span>
      </span>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry} className="shrink-0 gap-1.5">
          <RefreshCw className="h-3.5 w-3.5" /> Retry
        </Button>
      )}
    </div>
  );
}

export function Spinner({ label, className }: { label?: string; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2 text-sm text-gray-500', className)} role="status">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}
    </span>
  );
}

export function Skeleton({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('animate-pulse space-y-2', className)} aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-8 rounded bg-gray-100" style={{ width: `${100 - (i % 3) * 12}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-gray-300 bg-white p-8 text-center">
      <p className="font-medium text-gray-800">{title}</p>
      {children && <div className="mx-auto mt-1 max-w-xl text-sm text-gray-500">{children}</div>}
    </div>
  );
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

let openDialogs = 0;

/**
 * Modal focus: the dialog renders outside #root, which turns inert while any dialog is
 * open; focus moves in, Tab cycles inside, and focus returns to the opener on close.
 */
function useDialogFocus(panel: RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    const el = panel.current;
    if (!el) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const app = document.getElementById('root');
    openDialogs += 1;
    app?.setAttribute('inert', '');
    (el.querySelector<HTMLElement>('[data-autofocus]') ?? el).focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return;
      const items = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === el)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    el.addEventListener('keydown', onKey);
    return () => {
      el.removeEventListener('keydown', onKey);
      openDialogs -= 1;
      if (openDialogs === 0) app?.removeAttribute('inert');
      opener?.focus();
    };
  }, [panel]);
}

interface ShellProps {
  title: ReactNode;
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}

/** A centred dialog; Escape or the close button dismisses it. */
export function Modal({ title, subtitle, onClose, children, footer, wide }: ShellProps & { wide?: boolean }) {
  useEscape(onClose);
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  useDialogFocus(panel);
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cn('flex max-h-[90vh] w-full flex-col rounded-lg bg-white shadow-xl focus:outline-none', wide ? 'max-w-3xl' : 'max-w-xl')}
      >
        <ShellHeader titleId={titleId} title={title} subtitle={subtitle} onClose={onClose} />
        <div className="space-y-4 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t px-5 py-3">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

/** A panel sliding in from the right, for reviewing longer content next to the page. */
export function Drawer({ title, subtitle, onClose, children, footer }: ShellProps) {
  useEscape(onClose);
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  useDialogFocus(panel);
  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="flex h-full w-full max-w-2xl flex-col bg-white shadow-2xl focus:outline-none"
      >
        <ShellHeader titleId={titleId} title={title} subtitle={subtitle} onClose={onClose} />
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="border-t px-5 py-3">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

function ShellHeader({ titleId, title, subtitle, onClose }: {
  titleId: string;
  title: ReactNode;
  subtitle?: ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b px-5 py-4">
      <div className="min-w-0">
        <h3 id={titleId} className="text-lg font-semibold text-gray-900">{title}</h3>
        {subtitle && <div className="mt-1 text-sm text-gray-500">{subtitle}</div>}
      </div>
      <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label="Close">
        <X className="h-5 w-5" />
      </button>
    </div>
  );
}

export interface ToastMessage {
  id: number;
  text: string;
  link?: { to: string; label: string };
  kind?: 'success' | 'error';
}

/** A dismissible confirmation in the top-right corner that hides itself after a while. */
export function Toast({ toast, onDismiss }: { toast: ToastMessage; onDismiss: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(onDismiss, 10000);
    return () => window.clearTimeout(timer);
  }, [toast.id, onDismiss]);
  const error = toast.kind === 'error';
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        'fixed right-6 top-6 z-[60] flex max-w-md items-start gap-3 rounded-md border p-3 text-sm shadow-lg',
        error ? 'border-red-200 bg-red-50 text-red-800' : 'border-green-200 bg-green-50 text-green-800',
      )}
    >
      {error ? <XCircle className="mt-0.5 h-4 w-4 shrink-0" /> : <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />}
      <span className="min-w-0 flex-1">
        {toast.text}{' '}
        {toast.link && (
          <Link to={toast.link.to} className="font-medium underline">{toast.link.label}</Link>
        )}
      </span>
      <button type="button" onClick={onDismiss} aria-label="Dismiss" className="shrink-0 hover:opacity-70">
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

export function CopyButton({ value, label = 'Copy' }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be refused (e.g. an insecure origin); the value stays visible to copy by hand.
    }
  };
  return (
    <button type="button" onClick={copy} className="text-gray-400 hover:text-gray-700" aria-label={label} title={label}>
      {copied ? <Check className="h-3.5 w-3.5 text-green-600" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

export function SectionTitle({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between gap-3">
      <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-500">{children}</h3>
      {actions}
    </div>
  );
}

/** Previous / next controls for an offset-paged list. */
export function Pager({ offset, limit, total, onChange }: {
  offset: number;
  limit: number;
  total: number;
  onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;
  const last = Math.min(offset + limit, total);
  return (
    <div className="flex items-center justify-end gap-2 text-xs text-gray-500">
      <span>{offset + 1}–{last} of {total}</span>
      <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        Previous
      </Button>
      <Button size="sm" variant="outline" disabled={last >= total} onClick={() => onChange(offset + limit)}>
        Next
      </Button>
    </div>
  );
}
