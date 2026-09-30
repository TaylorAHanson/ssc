import { useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { format } from 'date-fns';
import {
    Activity, AlertTriangle, CheckCircle2, Clock, FileStack, Loader2, RefreshCw, Timer, Users, XCircle,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Switch } from '../../components/ui/switch';
import {
    ChartCard, CompletionByTypeChart, RequestsOverTimeChart, StatusDonut, TypeBarChart, type OverTimeSeries,
} from '../../components/admin/dashboard/DashboardCharts';
import { FilterChip, KpiCard, SegmentedControl } from '../../components/admin/dashboard/DashboardWidgets';
import { NeedsAttention } from '../../components/admin/dashboard/NeedsAttention';
import { RequestsTable, type SortState } from '../../components/admin/dashboard/RequestsTable';
import { useDashboardFilters } from '../../components/admin/dashboard/useDashboardFilters';
import { useDashboardRequests } from '../../components/admin/dashboard/useDashboardRequests';
import {
    DAY_MS, RANGE_OPTIONS, STATUS_FILTER_LABELS, STUCK_AFTER_HOURS,
    completionByType, computeKpis, createdInRange, formatDuration, inScope, isAutomated, matchesStatus,
    needsAttention, rangeDays, statusBreakdown, topRequesters, typeBreakdown, typeLabel,
    type StatusFilter,
} from '../../components/admin/dashboard/requestMetrics';
import type { Request } from '../../types';

const RANGE_SEGMENTS = RANGE_OPTIONS.map((o) => ({ key: o.key, label: o.days ? `${o.days}d` : 'All' }));
const SERIES_SEGMENTS: { key: OverTimeSeries; label: string }[] = [
    { key: 'status', label: 'By status' },
    { key: 'source', label: 'People vs automated' },
];
const DEFAULT_SORT: SortState = { key: 'createdAt', direction: 'desc' };

export function AdminDashboard() {
    const navigate = useNavigate();
    const [filters, setFilters] = useDashboardFilters();
    const days = rangeDays(filters.range);
    const { rows, fetchedAt, truncated, error, loading, reload } = useDashboardRequests(days);
    const now = fetchedAt ?? 0;
    const start = days === null || fetchedAt === null ? null : fetchedAt - days * DAY_MS;

    const [search, setSearch] = useState('');
    const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
    const [overTimeSeries, setOverTimeSeries] = useState<OverTimeSeries>('status');
    const tableRef = useRef<HTMLDivElement>(null);

    const scoped = useMemo(() => rows.filter((r) => inScope(r, start)), [rows, start]);
    const automatedCount = useMemo(() => scoped.filter(isAutomated).length, [scoped]);
    const visible = useMemo(
        () => (filters.showAutomated ? scoped : scoped.filter((r) => !isAutomated(r))),
        [scoped, filters.showAutomated],
    );

    const kpis = useMemo(() => computeKpis(visible, start, now), [visible, start, now]);
    const types = useMemo(() => typeBreakdown(visible), [visible]);
    const statuses = useMemo(() => statusBreakdown(visible), [visible]);
    const completion = useMemo(() => completionByType(visible, now), [visible, now]);
    const attention = useMemo(() => needsAttention(visible, now), [visible, now]);
    const requesters = useMemo(() => topRequesters(visible, 6), [visible]);
    const createdRows = useMemo(() => visible.filter((r) => createdInRange(r, start)), [visible, start]);

    const tableRows = useMemo(() => {
        const q = search.trim().toLowerCase();
        return visible.filter((r) =>
            (!filters.status || matchesStatus(r, filters.status, now))
            && (!filters.type || r.type === filters.type)
            && (!filters.requester || r.requester_email === filters.requester)
            && (!q || r.id.toLowerCase().includes(q) || r.title.toLowerCase().includes(q)
                || (r.requester_email ?? '').toLowerCase().includes(q)));
    }, [visible, filters.status, filters.type, filters.requester, search, now]);

    const focusTable = () => tableRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });

    const toggleStatus = (status: StatusFilter, nextSort?: SortState) => {
        const turningOn = filters.status !== status;
        setFilters({ status: turningOn ? status : null });
        if (turningOn) {
            if (nextSort) setSort(nextSort);
            focusTable();
        }
    };
    const toggleType = (type: string) => {
        const turningOn = filters.type !== type;
        setFilters({ type: turningOn ? type : null });
        if (turningOn) focusTable();
    };
    const toggleRequester = (email: string) => {
        const turningOn = filters.requester !== email;
        setFilters({ requester: turningOn ? email : null });
        if (turningOn) focusTable();
    };
    const clearTableFilters = () => {
        setFilters({ status: null, type: null, requester: null });
        setSearch('');
    };

    const openRequest = (r: Request, newTab = false) => {
        const path = `/requests/${encodeURIComponent(r.id)}`;
        if (newTab) window.open(path, '_blank', 'noopener');
        else navigate(path);
    };

    const series = filters.showAutomated ? overTimeSeries : 'status';
    const rangeLabel = days === null ? 'all time' : `the last ${days} days`;
    const hasTableFilters = !!(filters.status || filters.type || filters.requester || search.trim());

    const chips = hasTableFilters && (
        <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-gray-500">Filtered by</span>
            {filters.status && <FilterChip label={STATUS_FILTER_LABELS[filters.status]} onRemove={() => setFilters({ status: null })} />}
            {filters.type && <FilterChip label={`Type: ${typeLabel(filters.type)}`} onRemove={() => setFilters({ type: null })} />}
            {filters.requester && <FilterChip label={`Requester: ${filters.requester}`} onRemove={() => setFilters({ requester: null })} />}
            {search.trim() && <FilterChip label={`Search: “${search.trim()}”`} onRemove={() => setSearch('')} />}
            <button type="button" onClick={clearTableFilters} className="text-xs text-gray-500 hover:text-gray-900 hover:underline">
                Clear all
            </button>
        </div>
    );

    if (loading && fetchedAt === null) {
        return (
            <div className="flex items-center justify-center min-h-[300px] gap-2 text-gray-500">
                <Loader2 className="w-5 h-5 animate-spin" /> Loading dashboard…
            </div>
        );
    }

    return (
        <div className="space-y-5">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-200 bg-white px-4 py-3 shadow-sm">
                <div className="flex flex-wrap items-center gap-3">
                    <SegmentedControl options={RANGE_SEGMENTS} value={filters.range} onChange={(range) => setFilters({ range })} />
                    <span className="text-xs text-gray-500">
                        Requests created in {rangeLabel}, plus anything still open
                    </span>
                    {loading && <Loader2 className="w-4 h-4 animate-spin text-gray-400" />}
                </div>
                <div className="flex flex-wrap items-center gap-4">
                    <div
                        className="flex items-center gap-2"
                        title="Automated: policy-enforcement scans, scheduled reports, and requests with no requester."
                    >
                        <Switch
                            id="hide-automated"
                            checked={!filters.showAutomated}
                            onCheckedChange={(hide) => setFilters({ showAutomated: !hide })}
                        />
                        <label htmlFor="hide-automated" className="text-sm font-medium text-gray-800 cursor-pointer">Hide automated</label>
                        <span className="text-xs text-gray-500">
                            {automatedCount} automated {automatedCount === 1 ? 'run' : 'runs'} {filters.showAutomated ? 'included' : 'hidden'}
                        </span>
                    </div>
                    <div className="flex items-center gap-1 text-xs text-gray-500">
                        {fetchedAt !== null && <span>Updated {format(fetchedAt, 'HH:mm')}</span>}
                        <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={() => void reload()} aria-label="Refresh">
                            <RefreshCw className="w-4 h-4" />
                        </Button>
                    </div>
                </div>
            </div>

            {error && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
                    Couldn't refresh the dashboard: {error}
                </div>
            )}
            {truncated && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
                    Showing the most recent 1,000 requests of each kind; older requests aren't counted. Narrow the time range for complete numbers.
                </div>
            )}

            <div className="grid grid-cols-2 md:grid-cols-4 2xl:grid-cols-7 gap-3">
                <KpiCard
                    icon={FileStack} tone="blue" label="Requests" value={kpis.total}
                    hint={`${kpis.newInRange} created in ${days === null ? 'total' : `${days}d`}`}
                    active={!hasTableFilters}
                    onClick={() => { clearTableFilters(); focusTable(); }}
                />
                <KpiCard
                    icon={Clock} tone="amber" label="Awaiting approval" value={kpis.awaiting}
                    hint="Waiting on an approver"
                    active={filters.status === 'awaiting_approval'}
                    onClick={() => toggleStatus('awaiting_approval', { key: 'timeInState', direction: 'desc' })}
                />
                <KpiCard
                    icon={Activity} tone="indigo" label="In progress" value={kpis.inProgress}
                    hint="Running or waiting on a task"
                    active={filters.status === 'in_progress'}
                    onClick={() => toggleStatus('in_progress')}
                />
                <KpiCard
                    icon={AlertTriangle} tone={kpis.stuck ? 'orange' : 'gray'} label="Stuck" value={kpis.stuck}
                    hint={`> ${STUCK_AFTER_HOURS / 24} days in one step`}
                    active={filters.status === 'stuck'}
                    onClick={() => toggleStatus('stuck', { key: 'timeInState', direction: 'desc' })}
                />
                <KpiCard
                    icon={XCircle} tone={kpis.failed ? 'red' : 'gray'} label="Failed" value={kpis.failed}
                    hint={kpis.closed ? `${Math.round((kpis.failed / kpis.closed) * 100)}% of closed` : 'None closed yet'}
                    active={filters.status === 'failed'}
                    onClick={() => toggleStatus('failed', DEFAULT_SORT)}
                />
                <KpiCard
                    icon={Timer} tone="indigo" label="Median to complete" value={formatDuration(kpis.medianCompleteMs)}
                    hint={`${kpis.completed} completed`}
                    active={filters.status === 'completed'}
                    onClick={() => toggleStatus('completed', { key: 'duration', direction: 'desc' })}
                />
                <KpiCard
                    icon={CheckCircle2} tone="green" label="Success rate"
                    value={kpis.successRate === null ? '—' : `${Math.round(kpis.successRate * 100)}%`}
                    hint={`${kpis.completed} of ${kpis.closed} closed completed`}
                    active={filters.status === 'closed'}
                    onClick={() => toggleStatus('closed')}
                />
            </div>

            <NeedsAttention
                lists={attention}
                now={now}
                onOpen={(r) => openRequest(r)}
                onShowAll={(f) => toggleStatus(f, { key: 'timeInState', direction: 'desc' })}
            />

            <div className="grid gap-4 lg:grid-cols-3">
                <div className="lg:col-span-2">
                    <ChartCard
                        title="Requests over time"
                        subtitle={`Created per ${days === null || days > 45 ? 'week' : 'day'} · click a segment to filter by status`}
                        empty={createdRows.length === 0}
                        action={filters.showAutomated && (
                            <SegmentedControl size="sm" options={SERIES_SEGMENTS} value={overTimeSeries} onChange={setOverTimeSeries} />
                        )}
                    >
                        <RequestsOverTimeChart
                            rows={createdRows}
                            start={start}
                            now={now}
                            series={series}
                            onStatusClick={(g) => toggleStatus(g)}
                        />
                    </ChartCard>
                </div>
                <ChartCard title="Status breakdown" subtitle="Click a slice to filter" empty={statuses.length === 0}>
                    <StatusDonut
                        data={statuses}
                        selected={statuses.some((s) => s.group === filters.status) ? filters.status : null}
                        onStatusClick={(g) => toggleStatus(g)}
                    />
                </ChartCard>
            </div>

            <div className="grid gap-4 lg:grid-cols-3">
                <ChartCard title="Requests by type" subtitle="Click a bar to filter" empty={types.length === 0}>
                    <TypeBarChart data={types} selected={filters.type} onTypeClick={toggleType} />
                </ChartCard>
                <ChartCard title="Time to complete by type" subtitle="Median, completed requests only" empty={completion.length === 0}>
                    <CompletionByTypeChart data={completion} selected={filters.type} onTypeClick={toggleType} />
                </ChartCard>
                <TopRequesters
                    requesters={requesters}
                    selected={filters.requester}
                    onSelect={toggleRequester}
                />
            </div>

            <RequestsTable
                ref={tableRef}
                rows={tableRows}
                now={now}
                sort={sort}
                onSortChange={setSort}
                search={search}
                onSearchChange={setSearch}
                chips={chips}
                onOpen={openRequest}
                onRequesterClick={toggleRequester}
                onTypeClick={toggleType}
            />
        </div>
    );
}

function TopRequesters({ requesters, selected, onSelect }: {
    requesters: { email: string; count: number }[];
    selected: string | null;
    onSelect: (email: string) => void;
}) {
    const max = requesters[0]?.count ?? 1;
    return (
        <Card>
            <CardHeader className="p-4 pb-2">
                <CardTitle className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    <Users className="w-4 h-4 text-gray-500" /> Top requesters
                </CardTitle>
                <p className="text-xs text-gray-500">Click a person to filter</p>
            </CardHeader>
            <CardContent className="p-4 pt-0">
                {requesters.length === 0 ? (
                    <div className="h-40 flex items-center justify-center text-sm text-gray-400">No data for this view</div>
                ) : (
                    <ul className="space-y-1">
                        {requesters.map(({ email, count }) => (
                            <li key={email}>
                                <button
                                    type="button"
                                    onClick={() => onSelect(email)}
                                    aria-pressed={selected === email}
                                    className={`w-full text-left rounded-md px-2 py-1.5 transition-colors ${selected === email ? 'bg-primary/10' : 'hover:bg-gray-50'}`}
                                >
                                    <div className="flex items-center justify-between text-sm">
                                        <span className="truncate text-gray-800">{email}</span>
                                        <span className="text-gray-500 tabular-nums">{count}</span>
                                    </div>
                                    <div className="mt-1 h-1.5 rounded-full bg-gray-100">
                                        <div className="h-1.5 rounded-full bg-primary" style={{ width: `${(count / max) * 100}%` }} />
                                    </div>
                                </button>
                            </li>
                        ))}
                    </ul>
                )}
            </CardContent>
        </Card>
    );
}
