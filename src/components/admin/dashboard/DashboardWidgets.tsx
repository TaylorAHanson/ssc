import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { X } from 'lucide-react';
import { cn } from '../../../lib/utils';
import type { Request } from '../../../types';
import { STATUS_GROUP_META, statusGroup, statusLabel } from './requestMetrics';

export type KpiTone = 'blue' | 'amber' | 'orange' | 'red' | 'green' | 'indigo' | 'gray';

const TONE_ICON: Record<KpiTone, string> = {
    blue: 'bg-blue-50 text-blue-600',
    amber: 'bg-amber-50 text-amber-600',
    orange: 'bg-orange-50 text-orange-600',
    red: 'bg-red-50 text-red-600',
    green: 'bg-emerald-50 text-emerald-600',
    indigo: 'bg-indigo-50 text-indigo-600',
    gray: 'bg-gray-100 text-gray-500',
};

export function KpiCard({ icon: Icon, label, value, hint, tone, active, onClick }: {
    icon: LucideIcon;
    label: string;
    value: ReactNode;
    hint?: string;
    tone: KpiTone;
    active: boolean;
    onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            aria-pressed={active}
            className={cn(
                'text-left rounded-lg border bg-white p-4 shadow-sm transition-all hover:border-primary/50 hover:shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                active ? 'border-primary ring-1 ring-primary bg-primary/5' : 'border-gray-200',
            )}
        >
            <div className="flex items-start justify-between gap-2">
                <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">{label}</p>
                <span className={cn('p-1.5 rounded-md', TONE_ICON[tone])}>
                    <Icon className="w-4 h-4" />
                </span>
            </div>
            <p className="text-2xl font-bold text-gray-900 mt-1">{value}</p>
            {hint && <p className="text-xs text-gray-500 mt-0.5 truncate">{hint}</p>}
        </button>
    );
}

export function StatusBadge({ request }: { request: Request }) {
    const meta = STATUS_GROUP_META[statusGroup(request)];
    return (
        <span className={cn('inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium', meta.badge)}>
            {statusLabel(request)}
        </span>
    );
}

export function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
    return (
        <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 text-primary border border-primary/20 pl-2.5 pr-1 py-0.5 text-xs font-medium">
            {label}
            <button type="button" onClick={onRemove} className="rounded-full p-0.5 hover:bg-primary/20" aria-label={`Remove filter ${label}`}>
                <X className="w-3 h-3" />
            </button>
        </span>
    );
}

export function SegmentedControl<T extends string>({ options, value, onChange, size = 'md' }: {
    options: { key: T; label: string }[];
    value: T;
    onChange: (value: T) => void;
    size?: 'sm' | 'md';
}) {
    return (
        <div className="inline-flex rounded-md border border-gray-200 bg-gray-50 p-0.5">
            {options.map((o) => (
                <button
                    key={o.key}
                    type="button"
                    onClick={() => onChange(o.key)}
                    aria-pressed={value === o.key}
                    className={cn(
                        'rounded font-medium transition-colors',
                        size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-3 py-1 text-sm',
                        value === o.key ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900',
                    )}
                >
                    {o.label}
                </button>
            ))}
        </div>
    );
}
