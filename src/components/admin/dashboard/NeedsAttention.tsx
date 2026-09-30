import type { LucideIcon } from 'lucide-react';
import { AlertTriangle, CheckCircle2, Clock, XCircle } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../../ui/card';
import type { Request } from '../../../types';
import { StatusBadge } from './DashboardWidgets';
import {
    STUCK_AFTER_HOURS, currentStateName, formatDuration, parseTimestamp, timeInStateMs, typeLabel,
    type AttentionLists, type StatusFilter,
} from './requestMetrics';

const LIMIT = 5;

export function NeedsAttention({ lists, now, onOpen, onShowAll }: {
    lists: AttentionLists;
    now: number;
    onOpen: (request: Request) => void;
    onShowAll: (filter: StatusFilter) => void;
}) {
    const total = lists.stuck.length + lists.failed.length + lists.awaiting.length;
    if (total === 0) {
        return (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                <CheckCircle2 className="w-4 h-4" />
                Nothing needs attention: no stuck, failed, or waiting requests in this view.
            </div>
        );
    }

    const ago = (r: Request) => {
        const updated = parseTimestamp(r.updatedAt);
        return updated === null ? '' : `${formatDuration(now - updated)} ago`;
    };

    return (
        <Card>
            <CardHeader className="p-4 pb-2">
                <CardTitle className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    <AlertTriangle className="w-4 h-4 text-orange-500" /> Needs attention
                </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0 grid gap-4 md:grid-cols-3">
                <AttentionColumn
                    icon={Clock}
                    iconClass="text-orange-500"
                    title={`Stuck > ${STUCK_AFTER_HOURS / 24} days`}
                    items={lists.stuck}
                    detail={(r) => `${formatDuration(timeInStateMs(r, now))} in ${currentStateName(r)}`}
                    onOpen={onOpen}
                    onShowAll={() => onShowAll('stuck')}
                />
                <AttentionColumn
                    icon={XCircle}
                    iconClass="text-red-500"
                    title="Latest failures"
                    items={lists.failed}
                    detail={(r) => `Failed ${ago(r)}`}
                    onOpen={onOpen}
                    onShowAll={() => onShowAll('failed')}
                />
                <AttentionColumn
                    icon={Clock}
                    iconClass="text-amber-500"
                    title="Oldest awaiting approval"
                    items={lists.awaiting}
                    detail={(r) => `Waiting ${formatDuration(timeInStateMs(r, now))}`}
                    onOpen={onOpen}
                    onShowAll={() => onShowAll('awaiting_approval')}
                />
            </CardContent>
        </Card>
    );
}

function AttentionColumn({ icon: Icon, iconClass, title, items, detail, onOpen, onShowAll }: {
    icon: LucideIcon;
    iconClass: string;
    title: string;
    items: Request[];
    detail: (r: Request) => string;
    onOpen: (r: Request) => void;
    onShowAll: () => void;
}) {
    return (
        <div className="min-w-0">
            <div className="flex items-center justify-between mb-1.5">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 flex items-center gap-1.5">
                    <Icon className={`w-3.5 h-3.5 ${iconClass}`} /> {title}
                    <span className="text-gray-400 font-normal normal-case">({items.length})</span>
                </h4>
                {items.length > LIMIT && (
                    <button type="button" onClick={onShowAll} className="text-xs text-primary hover:underline">
                        View all
                    </button>
                )}
            </div>
            {items.length === 0 ? (
                <p className="text-sm text-gray-400 py-2">None</p>
            ) : (
                <ul className="divide-y divide-gray-100 rounded-md border border-gray-100">
                    {items.slice(0, LIMIT).map((r) => (
                        <li key={r.id}>
                            <button
                                type="button"
                                onClick={() => onOpen(r)}
                                className="w-full text-left px-3 py-2 hover:bg-gray-50 transition-colors"
                            >
                                <div className="flex items-center justify-between gap-2">
                                    <span className="text-sm font-medium text-gray-900 truncate">{r.title}</span>
                                    <StatusBadge request={r} />
                                </div>
                                <div className="text-xs text-gray-500 truncate mt-0.5">
                                    {typeLabel(r.type)} · {detail(r)}{r.requester_email ? ` · ${r.requester_email}` : ''}
                                </div>
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
