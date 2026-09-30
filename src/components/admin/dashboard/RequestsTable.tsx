import { forwardRef, useMemo, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react';
import { format } from 'date-fns';
import { Activity, CheckSquare, ChevronDown, ChevronUp, Filter, Search, SlidersHorizontal, Square } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../../ui/card';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import type { Request } from '../../../types';
import { StatusBadge } from './DashboardWidgets';
import {
    STATUS_GROUP_ORDER, currentStateName, durationMs, formatDuration, isAutomated, isStuck,
    parseTimestamp, statusGroup, statusLabel, timeInStateMs, typeLabel,
} from './requestMetrics';

export type SortKey =
    | 'id' | 'title' | 'type' | 'status' | 'currentState' | 'requester_email' | 'timeInState' | 'duration' | 'createdAt';

export interface SortState { key: SortKey; direction: 'asc' | 'desc' }

interface Column {
    id: SortKey;
    label: string;
    /** Text used by the per-column filter row; omitted for columns without one. */
    filterText?: (r: Request) => string;
    sortValue: (r: Request, now: number) => string | number;
}

const COLUMNS: Column[] = [
    { id: 'id', label: 'ID', filterText: (r) => r.id, sortValue: (r) => r.id },
    { id: 'title', label: 'Title', filterText: (r) => r.title, sortValue: (r) => r.title.toLowerCase() },
    { id: 'type', label: 'Type', filterText: (r) => typeLabel(r.type), sortValue: (r) => r.type },
    { id: 'status', label: 'Status', filterText: statusLabel, sortValue: (r) => STATUS_GROUP_ORDER.indexOf(statusGroup(r)) },
    { id: 'currentState', label: 'Current step', filterText: currentStateName, sortValue: (r) => currentStateName(r) },
    { id: 'requester_email', label: 'Requested by', filterText: (r) => r.requester_email ?? '', sortValue: (r) => r.requester_email ?? '' },
    { id: 'timeInState', label: 'Time in state', sortValue: (r, now) => timeInStateMs(r, now) ?? -1 },
    { id: 'duration', label: 'Duration', sortValue: (r, now) => durationMs(r, now) ?? -1 },
    { id: 'createdAt', label: 'Created', sortValue: (r) => parseTimestamp(r.createdAt) ?? 0 },
];

const DEFAULT_VISIBLE: SortKey[] = ['title', 'type', 'status', 'currentState', 'requester_email', 'timeInState', 'createdAt'];

interface RequestsTableProps {
    rows: Request[];
    now: number;
    sort: SortState;
    onSortChange: (sort: SortState) => void;
    search: string;
    onSearchChange: (value: string) => void;
    chips: ReactNode;
    onOpen: (request: Request, newTab: boolean) => void;
    onRequesterClick: (email: string) => void;
    onTypeClick: (type: string) => void;
}

export const RequestsTable = forwardRef<HTMLDivElement, RequestsTableProps>(function RequestsTable(
    { rows, now, sort, onSortChange, search, onSearchChange, chips, onOpen, onRequesterClick, onTypeClick },
    ref,
) {
    const [visible, setVisible] = useState<Set<SortKey>>(new Set(DEFAULT_VISIBLE));
    const [columnFilters, setColumnFilters] = useState<Partial<Record<SortKey, string>>>({});
    const [showColumnSelector, setShowColumnSelector] = useState(false);
    const [showFilters, setShowFilters] = useState(false);

    const columns = COLUMNS.filter((c) => visible.has(c.id));

    const toggleColumn = (id: SortKey) => {
        const next = new Set(visible);
        if (next.has(id)) {
            if (next.size > 1) next.delete(id);
        } else {
            next.add(id);
        }
        setVisible(next);
    };

    const handleSort = (key: SortKey) => {
        const direction = sort.key === key && sort.direction === 'desc' ? 'asc' : 'desc';
        onSortChange({ key, direction });
    };

    const displayed = useMemo(() => {
        const active = COLUMNS.filter((c) => c.filterText && columnFilters[c.id]);
        const filtered = rows.filter((r) => active.every((c) =>
            c.filterText!(r).toLowerCase().includes(columnFilters[c.id]!.toLowerCase())));
        const column = COLUMNS.find((c) => c.id === sort.key)!;
        const dir = sort.direction === 'asc' ? 1 : -1;
        return filtered.sort((a, b) => {
            const av = column.sortValue(a, now);
            const bv = column.sortValue(b, now);
            return av < bv ? -dir : av > bv ? dir : 0;
        });
    }, [rows, columnFilters, sort, now]);

    const openRow = (r: Request) => (e: MouseEvent) => onOpen(r, e.metaKey || e.ctrlKey);
    const onRowKey = (r: Request) => (e: KeyboardEvent) => {
        if (e.key === 'Enter') onOpen(r, e.metaKey || e.ctrlKey);
    };
    const stop = (fn: () => void) => (e: MouseEvent) => {
        e.stopPropagation();
        fn();
    };

    const renderCell = (r: Request, id: SortKey) => {
        switch (id) {
            case 'id':
                return <td key={id} className="p-3 font-mono text-xs text-gray-500">{r.id.slice(0, 12)}…</td>;
            case 'title':
                return (
                    <td key={id} className="p-3 font-medium text-gray-900 max-w-xs">
                        <div className="flex items-center gap-2">
                            <span className="truncate">{r.title}</span>
                            {isAutomated(r) && <span className="shrink-0 rounded bg-violet-50 border border-violet-200 px-1.5 text-[10px] font-medium uppercase text-violet-700">auto</span>}
                        </div>
                    </td>
                );
            case 'type':
                return (
                    <td key={id} className="p-3 whitespace-nowrap">
                        <button type="button" onClick={stop(() => onTypeClick(r.type))} className="text-gray-600 hover:text-primary hover:underline text-left">
                            {typeLabel(r.type)}
                        </button>
                    </td>
                );
            case 'status':
                return <td key={id} className="p-3"><StatusBadge request={r} /></td>;
            case 'currentState':
                return <td key={id} className="p-3 text-xs text-gray-600">{currentStateName(r)}</td>;
            case 'requester_email':
                return (
                    <td key={id} className="p-3">
                        {r.requester_email ? (
                            <button type="button" onClick={stop(() => onRequesterClick(r.requester_email!))} className="text-gray-600 hover:text-primary hover:underline text-left">
                                {r.requester_email}
                            </button>
                        ) : <span className="text-gray-400">System</span>}
                    </td>
                );
            case 'timeInState':
                return (
                    <td key={id} className={`p-3 font-mono text-xs ${isStuck(r, now) ? 'text-orange-600 font-semibold' : 'text-gray-600'}`}>
                        {formatDuration(timeInStateMs(r, now))}
                    </td>
                );
            case 'duration':
                return <td key={id} className="p-3 font-mono text-xs text-gray-600">{formatDuration(durationMs(r, now))}</td>;
            case 'createdAt': {
                const created = parseTimestamp(r.createdAt);
                return <td key={id} className="p-3 text-gray-500 whitespace-nowrap">{created === null ? '—' : format(created, 'MMM d, HH:mm')}</td>;
            }
        }
    };

    return (
        <Card ref={ref} className="flex flex-col h-[640px] scroll-mt-4">
            <CardHeader className="p-4 border-b border-gray-200 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <CardTitle className="text-base flex items-center gap-2">
                        <Activity className="w-4 h-4" /> Requests
                        <span className="text-sm font-normal text-gray-500">({displayed.length})</span>
                    </CardTitle>
                    <div className="flex items-center gap-2">
                        <div className="relative">
                            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-500" />
                            <Input
                                placeholder="Search title, ID, requester…"
                                value={search}
                                onChange={(e) => onSearchChange(e.target.value)}
                                className="pl-8 w-64 h-9"
                            />
                        </div>
                        <Button variant={showFilters ? 'default' : 'outline'} size="sm" className="h-9 gap-1" onClick={() => setShowFilters(!showFilters)}>
                            <Filter className="w-4 h-4" /> Filter
                        </Button>
                        <div className="relative">
                            <Button variant="outline" size="sm" className="h-9 gap-1" onClick={() => setShowColumnSelector(!showColumnSelector)}>
                                <SlidersHorizontal className="w-4 h-4" /> Columns
                            </Button>
                            {showColumnSelector && (
                                <div className="absolute right-0 top-full mt-2 w-48 bg-white border border-gray-200 rounded-lg shadow-lg z-20 p-2 space-y-1">
                                    <div className="text-xs font-semibold text-gray-500 uppercase px-2 mb-1">Visible columns</div>
                                    {COLUMNS.map((col) => (
                                        <button
                                            key={col.id}
                                            type="button"
                                            className="w-full flex items-center gap-2 px-2 py-1.5 hover:bg-gray-50 rounded text-sm"
                                            onClick={() => toggleColumn(col.id)}
                                        >
                                            {visible.has(col.id) ? <CheckSquare className="w-4 h-4 text-primary" /> : <Square className="w-4 h-4 text-gray-300" />}
                                            <span>{col.label}</span>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                </div>
                {chips}
            </CardHeader>
            <CardContent className="p-0 flex-1 overflow-auto relative">
                <table className="w-full text-sm text-left">
                    <thead className="bg-gray-50 text-gray-900 font-medium sticky top-0 z-10 shadow-sm">
                        <tr>
                            {columns.map((c) => <SortableHeader key={c.id} label={c.label} sortKey={c.id} sort={sort} onSort={handleSort} />)}
                        </tr>
                        {showFilters && (
                            <tr className="bg-gray-100/50">
                                {columns.map((c) => (
                                    <th key={c.id} className="p-2">
                                        {c.filterText && (
                                            <Input
                                                className="h-7 text-xs"
                                                placeholder={`Filter ${c.label.toLowerCase()}…`}
                                                value={columnFilters[c.id] ?? ''}
                                                onChange={(e) => setColumnFilters((prev) => ({ ...prev, [c.id]: e.target.value }))}
                                            />
                                        )}
                                    </th>
                                ))}
                            </tr>
                        )}
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                        {displayed.map((r) => (
                            <tr
                                key={r.id}
                                tabIndex={0}
                                onClick={openRow(r)}
                                onKeyDown={onRowKey(r)}
                                className="hover:bg-gray-50 cursor-pointer transition-colors focus:outline-none focus-visible:bg-primary/5"
                            >
                                {columns.map((c) => renderCell(r, c.id))}
                            </tr>
                        ))}
                    </tbody>
                </table>
                {displayed.length === 0 && (
                    <div className="py-16 text-center text-sm text-gray-400">No requests match these filters.</div>
                )}
            </CardContent>
        </Card>
    );
});

function SortableHeader({ label, sortKey, sort, onSort }: {
    label: string;
    sortKey: SortKey;
    sort: SortState;
    onSort: (key: SortKey) => void;
}) {
    const active = sort.key === sortKey;
    return (
        <th
            className="p-3 cursor-pointer hover:bg-gray-100 transition-colors whitespace-nowrap"
            onClick={() => onSort(sortKey)}
            aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
        >
            <div className="flex items-center gap-1">
                {label}
                <div className="flex flex-col">
                    <ChevronUp className={`w-3 h-3 -mb-1 ${active && sort.direction === 'asc' ? 'text-gray-900' : 'text-gray-300'}`} />
                    <ChevronDown className={`w-3 h-3 ${active && sort.direction === 'desc' ? 'text-gray-900' : 'text-gray-300'}`} />
                </div>
            </div>
        </th>
    );
}
