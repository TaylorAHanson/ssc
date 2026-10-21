import { formatInTimeZone } from 'date-fns-tz';
import { parseUtcDate } from '../../lib/requestDates';
import type { Approval, Request } from '../../types';

/** An inbox item, with the backend's per-user flags. */
export type InboxApproval = Approval & {
  /** Display name of the workflow behind `requestType`, when it has one. */
  workflowName?: string | null;
};

export type View = 'approvals' | 'mine' | 'all';
export type ApprovalStatusFilter = 'pending' | 'done' | 'all';
export type RequestStatusFilter = 'active' | 'completed' | 'failed' | 'all';
export type StatusFilter = ApprovalStatusFilter | RequestStatusFilter;

export const SELF_APPROVAL_MESSAGE = "You can't approve your own request. Another approver must.";
export const BULK_APPROVE_MAX = 100;

// Aging. A manual task can park a request indefinitely — there is no timeout and
// nothing chases it — so "how long has this been sitting here" has to be visible
// in the list rather than something you only learn when the requester asks.
export const AGING_DAYS = 3;
export const STALE_DAYS = 7;

export const ROLE_LABELS: Record<string, string> = {
  platform_admin: 'Platform Admin',
  data_owner: 'Data Owner',
  manager: 'Manager',
  governance_admin: 'Governance Admin',
  security_admin: 'Security Admin',
  finance_admin: 'Finance Admin',
  // Not a role: a manual_task item is work assigned to you, not an approval
  // you grant. Labeling it "Manual Task" keeps that distinction on screen.
  manual_task: 'Manual Task',
};

export const typeLabel = (type: string) => {
  const words = (type || '').replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
};

export const workflowLabel = (a: InboxApproval) => a.workflowName?.trim() || typeLabel(a.requestType);

export const approverLabel = (a: Approval) =>
  a.approverLabel || ROLE_LABELS[a.approvalType] || a.approvalType.replace(/_/g, ' ');

export const isOverdue = (dueAt: string) => parseUtcDate(dueAt).getTime() < Date.now();

export const waitingDays = (createdAt: string) => {
  const started = parseUtcDate(createdAt).getTime();
  if (!Number.isFinite(started)) return 0;
  return Math.floor((Date.now() - started) / 86_400_000);
};

export const shortDate = (iso: string) => formatInTimeZone(parseUtcDate(iso), 'America/Los_Angeles', 'MMM d');

/** "5m", "3h", "12d": enough to scan a column; the cell's title has the full time. */
export const shortAge = (iso: string) => {
  const ms = Date.now() - parseUtcDate(iso).getTime();
  if (!Number.isFinite(ms)) return '';
  const minutes = Math.max(0, Math.floor(ms / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
};

// Manual tasks are confirmed one at a time against their instructions, so they
// stay out of bulk approval.
export const isBulkCandidate = (a: Approval) => a.status === 'pending' && a.approvalType !== 'manual_task';

export const isRequestActive = (r: Request) => !['completed', 'rejected', 'failed'].includes(r.status);

export const requestStatusMatches = (r: Request, filter: RequestStatusFilter) => {
  if (filter === 'active') return isRequestActive(r);
  if (filter === 'completed') return r.status === 'completed';
  if (filter === 'failed') return r.status === 'rejected' || r.status === 'failed';
  return true;
};

export const approvalStatusMatches = (a: Approval, filter: ApprovalStatusFilter) => {
  if (filter === 'pending') return a.status === 'pending';
  if (filter === 'done') return a.status !== 'pending';
  return true;
};

export function approvalStatus(a: Approval): { label: string; tone: string } {
  const manual = a.approvalType === 'manual_task';
  switch (a.status) {
    case 'pending':
      return { label: manual ? 'Task for you' : 'Needs approval', tone: 'bg-amber-100 text-amber-800' };
    case 'approved':
      return { label: manual ? 'Done' : 'Approved', tone: 'bg-green-100 text-green-800' };
    case 'rejected':
      return { label: manual ? "Couldn't complete" : 'Rejected', tone: 'bg-red-100 text-red-800' };
    case 'superseded':
      return { label: 'Superseded by edit', tone: 'bg-orange-100 text-orange-800' };
    default:
      return { label: 'Delegated', tone: 'bg-blue-100 text-blue-800' };
  }
}

export function requestStatusTone(status: string): string {
  if (status === 'completed') return 'bg-green-100 text-green-800';
  if (status === 'provisioning') return 'bg-blue-100 text-blue-800';
  if (status === 'manager_approval' || status === 'data_owner_approval') return 'bg-yellow-100 text-yellow-800';
  if (status === 'training_pending') return 'bg-orange-100 text-orange-800';
  if (status === 'manual_task_pending') return 'bg-amber-100 text-amber-800';
  if (status === 'failed' || status === 'rejected') return 'bg-red-100 text-red-800';
  return 'bg-gray-100 text-gray-800';
}

/** Who a request is waiting on, from its pending approvals, e.g. "Waiting on jane@x.com". */
export function waitingOn(r: Request): string | null {
  const who = (r.approvals ?? [])
    .filter((a) => a.status === 'pending')
    .map((a) => a.assignedToEmail || a.approverLabel || ROLE_LABELS[a.approvalType] || a.assignedToRole)
    .filter((w): w is string => !!w);
  if (who.length === 0) return null;
  const unique = [...new Set(who)];
  return `Waiting on ${unique.slice(0, 2).join(', ')}${unique.length > 2 ? ` +${unique.length - 2}` : ''}`;
}

// Search covers the values a person would type (names, catalogs, tables, emails),
// so only short scalars are indexed: a request's metadata can hold thousands of
// records (e.g. a Sentinel run) that would make every keystroke slow.
const MAX_INDEXED_VALUE = 300;
function scalarValues(obj: Record<string, unknown> | undefined, out: string[]) {
  if (!obj) return;
  for (const v of Object.values(obj)) {
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
      out.push(String(v).slice(0, MAX_INDEXED_VALUE));
    } else if (Array.isArray(v) && v.length <= 20) {
      for (const item of v) {
        if (typeof item === 'string' || typeof item === 'number') out.push(String(item).slice(0, MAX_INDEXED_VALUE));
      }
    }
  }
}

export function approvalHaystack(a: InboxApproval): string {
  const parts = [
    a.id, a.requestId, a.requestTitle, a.requestType, workflowLabel(a),
    a.requestedBy, a.requestedByEmail, approvalStatus(a).label, a.status,
    approverLabel(a), a.assignedToEmail ?? '', a.assignedToRole ?? '', a.instructions ?? '',
  ];
  scalarValues(a.workflowParameters, parts);
  return parts.join('\n').toLowerCase();
}

export function requestHaystack(r: Request, workflow: string): string {
  const parts = [
    r.id, r.title, r.type, workflow, r.status, r.status.replace(/_/g, ' '),
    r.requester_email ?? '', r.environment ?? '', waitingOn(r) ?? '',
  ];
  scalarValues(r.metadata, parts);
  return parts.join('\n').toLowerCase();
}
