import type { Request } from '../../../types';
import { theme } from '../../../theme';

/** Request types the platform creates on its own schedule rather than for a person. */
export const AUTOMATED_REQUEST_TYPES: ReadonlySet<string> = new Set([
    'enforcement_sentinel', // policy-enforcement scans (scheduled and manual policy checks)
    'report_execution', // scheduled report subscriptions
]);

export function isAutomated(r: Request): boolean {
    return AUTOMATED_REQUEST_TYPES.has(r.type) || !r.requester_email?.trim();
}

export const HOUR_MS = 3_600_000;
export const DAY_MS = 24 * HOUR_MS;
export const STUCK_AFTER_HOURS = 72;

/** Parse a backend timestamp; offset-less values are stored and served as UTC. */
export function parseTimestamp(ts: string | null | undefined): number | null {
    if (!ts) return null;
    const normalized = ts.replace(' ', 'T');
    const iso = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(normalized) ? normalized : `${normalized}Z`;
    const ms = Date.parse(iso);
    return Number.isNaN(ms) ? null : ms;
}

const isNumber = (v: number | null): v is number => v !== null;

// --- Status ---------------------------------------------------------------

export type StatusGroup = 'awaiting_approval' | 'in_progress' | 'completed' | 'rejected' | 'failed';

export const STATUS_GROUP_ORDER: StatusGroup[] = ['awaiting_approval', 'in_progress', 'completed', 'rejected', 'failed'];

export const STATUS_GROUP_META: Record<StatusGroup, { label: string; color: string; badge: string }> = {
    awaiting_approval: { label: 'Awaiting approval', color: '#F59E0B', badge: 'bg-amber-50 text-amber-800 border-amber-200' },
    in_progress: { label: 'In progress', color: theme.colors.accent, badge: 'bg-blue-50 text-blue-700 border-blue-200' },
    completed: { label: 'Completed', color: '#10B981', badge: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
    rejected: { label: 'Rejected', color: '#9CA3AF', badge: 'bg-gray-100 text-gray-700 border-gray-200' },
    failed: { label: 'Failed', color: '#EF4444', badge: 'bg-red-50 text-red-700 border-red-200' },
};

const TERMINAL_STATUSES = new Set(['completed', 'rejected', 'failed']);

export const isOpen = (r: Request): boolean => !TERMINAL_STATUSES.has(r.status);

function hasPendingApproval(r: Request): boolean {
    return (r.approvals ?? []).some((a) => a.status === 'pending' && a.approvalType !== 'manual_task');
}

export function statusGroup(r: Request): StatusGroup {
    if (r.status === 'completed' || r.status === 'rejected' || r.status === 'failed') return r.status;
    if (r.status.includes('approval') || hasPendingApproval(r)) return 'awaiting_approval';
    return 'in_progress';
}

const STATUS_LABELS: Record<string, string> = {
    pending: 'In progress',
    provisioning: 'Provisioning',
    manager_approval: 'Manager approval',
    data_owner_approval: 'Data owner approval',
    training_pending: 'Training',
    manual_task_pending: 'Manual task',
};

export function statusLabel(r: Request): string {
    return STATUS_LABELS[r.status] ?? STATUS_GROUP_META[statusGroup(r)].label;
}

export function currentStateName(r: Request): string {
    if (!isOpen(r)) return STATUS_GROUP_META[statusGroup(r)].label;
    return r.stateMachine?.states?.find((s) => s.isActive)?.name ?? '—';
}

export function typeLabel(type: string): string {
    const spaced = type.replace(/_/g, ' ');
    return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

// --- Durations ------------------------------------------------------------

/**
 * When the request entered its current state. Active states often lack a
 * `startedAt`, so fall back to the oldest pending approval, then the latest
 * completed step, then creation.
 */
export function stateEnteredAt(r: Request): number | null {
    const states = r.stateMachine?.states ?? [];
    const started = parseTimestamp(states.find((s) => s.isActive)?.startedAt);
    if (started !== null) return started;
    const pending = (r.approvals ?? [])
        .filter((a) => a.status === 'pending')
        .map((a) => parseTimestamp(a.createdAt))
        .filter(isNumber);
    if (pending.length) return Math.min(...pending);
    const completed = states.map((s) => parseTimestamp(s.completedAt)).filter(isNumber);
    if (completed.length) return Math.max(...completed);
    return parseTimestamp(r.createdAt);
}

export function timeInStateMs(r: Request, now: number): number | null {
    if (!isOpen(r)) return null;
    const entered = stateEnteredAt(r);
    return entered === null ? null : Math.max(0, now - entered);
}

export function isStuck(r: Request, now: number): boolean {
    const ms = timeInStateMs(r, now);
    return ms !== null && ms > STUCK_AFTER_HOURS * HOUR_MS;
}

/** Time to close for finished requests; age so far for open ones. */
export function durationMs(r: Request, now: number): number | null {
    const created = parseTimestamp(r.createdAt);
    if (created === null) return null;
    const end = isOpen(r) ? now : parseTimestamp(r.updatedAt);
    return end === null ? null : Math.max(0, end - created);
}

export function formatDuration(ms: number | null): string {
    if (ms === null) return '—';
    const minutes = Math.round(ms / 60_000);
    if (minutes < 1) return '<1m';
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ${minutes % 60}m`;
    const days = Math.floor(hours / 24);
    return `${days}d ${hours % 24}h`;
}

export function median(values: number[]): number | null {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// --- Range & filters ------------------------------------------------------

export type RangeKey = '7d' | '30d' | '90d' | 'all';

export const RANGE_OPTIONS: { key: RangeKey; label: string; days: number | null }[] = [
    { key: '7d', label: '7 days', days: 7 },
    { key: '30d', label: '30 days', days: 30 },
    { key: '90d', label: '90 days', days: 90 },
    { key: 'all', label: 'All time', days: null },
];

export function rangeDays(range: RangeKey): number | null {
    return RANGE_OPTIONS.find((o) => o.key === range)?.days ?? null;
}

export function rangeStart(range: RangeKey, now: number): number | null {
    const days = rangeDays(range);
    return days === null ? null : now - days * DAY_MS;
}

export function createdInRange(r: Request, start: number | null): boolean {
    if (start === null) return true;
    const created = parseTimestamp(r.createdAt);
    return created !== null && created >= start;
}

/** A request belongs on the page if it was created in range or is still open. */
export function inScope(r: Request, start: number | null): boolean {
    return isOpen(r) || createdInRange(r, start);
}

export type StatusFilter = StatusGroup | 'stuck' | 'closed';

export const STATUS_FILTER_LABELS: Record<StatusFilter, string> = {
    ...Object.fromEntries(STATUS_GROUP_ORDER.map((g) => [g, STATUS_GROUP_META[g].label])) as Record<StatusGroup, string>,
    stuck: `Stuck > ${STUCK_AFTER_HOURS / 24} days`,
    closed: 'Closed',
};

export function isStatusFilter(v: string | null): v is StatusFilter {
    return v !== null && v in STATUS_FILTER_LABELS;
}

export function matchesStatus(r: Request, filter: StatusFilter, now: number): boolean {
    if (filter === 'stuck') return isStuck(r, now);
    if (filter === 'closed') return !isOpen(r);
    return statusGroup(r) === filter;
}

// --- Aggregates -----------------------------------------------------------

export interface Kpis {
    total: number;
    newInRange: number;
    awaiting: number;
    inProgress: number;
    stuck: number;
    failed: number;
    completed: number;
    closed: number;
    medianCompleteMs: number | null;
    successRate: number | null;
}

export function computeKpis(rows: Request[], start: number | null, now: number): Kpis {
    const groups = rows.map(statusGroup);
    const count = (g: StatusGroup) => groups.filter((x) => x === g).length;
    const completed = count('completed');
    const closed = completed + count('rejected') + count('failed');
    const completeTimes = rows
        .filter((r) => r.status === 'completed')
        .map((r) => durationMs(r, now))
        .filter(isNumber);
    return {
        total: rows.length,
        newInRange: rows.filter((r) => createdInRange(r, start)).length,
        awaiting: count('awaiting_approval'),
        inProgress: count('in_progress'),
        stuck: rows.filter((r) => isStuck(r, now)).length,
        failed: count('failed'),
        completed,
        closed,
        medianCompleteMs: median(completeTimes),
        successRate: closed ? completed / closed : null,
    };
}

export type TypeCount = { type: string; label: string; count: number };

export function typeBreakdown(rows: Request[]): TypeCount[] {
    const counts = new Map<string, number>();
    rows.forEach((r) => counts.set(r.type, (counts.get(r.type) ?? 0) + 1));
    return [...counts.entries()]
        .map(([type, count]) => ({ type, label: typeLabel(type), count }))
        .sort((a, b) => b.count - a.count);
}

export type StatusCount = { group: StatusGroup; label: string; count: number };

export function statusBreakdown(rows: Request[]): StatusCount[] {
    return STATUS_GROUP_ORDER
        .map((group) => ({ group, label: STATUS_GROUP_META[group].label, count: rows.filter((r) => statusGroup(r) === group).length }))
        .filter((s) => s.count > 0);
}

export type TypeCompletion = { type: string; label: string; medianHours: number; display: string; count: number };

export function completionByType(rows: Request[], now: number): TypeCompletion[] {
    const byType = new Map<string, number[]>();
    rows.filter((r) => r.status === 'completed').forEach((r) => {
        const ms = durationMs(r, now);
        if (ms === null) return;
        byType.set(r.type, [...(byType.get(r.type) ?? []), ms]);
    });
    return [...byType.entries()]
        .map(([type, times]) => {
            const med = median(times) ?? 0;
            return { type, label: typeLabel(type), medianHours: med / HOUR_MS, display: formatDuration(med), count: times.length };
        })
        .sort((a, b) => b.medianHours - a.medianHours);
}

export function topRequesters(rows: Request[], limit: number): { email: string; count: number }[] {
    const counts = new Map<string, number>();
    rows.forEach((r) => {
        const email = r.requester_email?.trim();
        if (email) counts.set(email, (counts.get(email) ?? 0) + 1);
    });
    return [...counts.entries()]
        .map(([email, count]) => ({ email, count }))
        .sort((a, b) => b.count - a.count)
        .slice(0, limit);
}

export interface AttentionLists { stuck: Request[]; failed: Request[]; awaiting: Request[] }

export function needsAttention(rows: Request[], now: number): AttentionLists {
    const byTimeInState = (a: Request, b: Request) => (timeInStateMs(b, now) ?? 0) - (timeInStateMs(a, now) ?? 0);
    const stuck = rows.filter((r) => isStuck(r, now)).sort(byTimeInState);
    const failed = rows
        .filter((r) => r.status === 'failed')
        .sort((a, b) => (parseTimestamp(b.updatedAt) ?? 0) - (parseTimestamp(a.updatedAt) ?? 0));
    const awaiting = rows
        .filter((r) => statusGroup(r) === 'awaiting_approval' && !isStuck(r, now))
        .sort(byTimeInState);
    return { stuck, failed, awaiting };
}
