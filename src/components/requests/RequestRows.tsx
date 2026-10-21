import type { ReactNode } from 'react';
import {
  AlertCircle, Check, ChevronRight, ClipboardCheck, Eye, FileText, Loader2, Pencil, Settings2, Trash2, X,
} from 'lucide-react';
import { Button } from '../ui/button';
import { StepReportPanel } from '../StepReportPanel';
import { formatDate } from '../../lib/requestDates';
import { cn } from '../../lib/utils';
import type { Request } from '../../types';
import {
  AGING_DAYS, SELF_APPROVAL_MESSAGE, STALE_DAYS, approvalStatus, approverLabel, isBulkCandidate, isOverdue,
  requestStatusTone, shortAge, shortDate, waitingDays, waitingOn, workflowLabel, type InboxApproval,
} from './rows';
import type { ApprovalDialogAction } from './ApprovalDialogs';

const chip = 'inline-flex items-center gap-1 rounded px-1.5 py-px text-[11px] font-medium leading-4';

function CopyId({ id }: { id: string }) {
  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); navigator.clipboard?.writeText(id); }}
      title={`${id} (click to copy)`}
      className="font-mono text-[11px] text-gray-500 hover:text-primary truncate max-w-[11rem]"
    >
      {id}
    </button>
  );
}

function StatusBadge({ label, tone }: { label: string; tone: string }) {
  return <span className={cn('inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium capitalize', tone)}>{label}</span>;
}

function TitleCell({ title, id, chevron, children }: { title: string; id: string; chevron?: boolean | null; children?: ReactNode }) {
  return (
    <div className="flex items-start gap-1.5 min-w-0">
      {chevron !== null && chevron !== undefined && (
        <ChevronRight
          aria-hidden="true"
          className={cn('w-4 h-4 mt-0.5 shrink-0 text-gray-400 transition-transform', chevron && 'rotate-90')}
        />
      )}
      <div className="min-w-0">
        <p className="truncate font-medium text-gray-900" title={title}>{title}</p>
        <div className="flex flex-wrap items-center gap-1.5">
          <CopyId id={id} />
          {children}
        </div>
      </div>
    </div>
  );
}

export function ApprovalRow({
  approval: a,
  colSpan,
  expanded,
  selectable,
  selected,
  onToggleExpand,
  onToggleSelect,
  onAction,
  onViewDetails,
  canEdit,
  detailsError,
}: {
  approval: InboxApproval;
  colSpan: number;
  expanded: boolean;
  /** Render the selection cell (the approvals view always does, for alignment). */
  selectable: boolean;
  selected: boolean;
  onToggleExpand: () => void;
  onToggleSelect: () => void;
  onAction: (action: ApprovalDialogAction) => void;
  onViewDetails: () => void;
  canEdit: boolean;
  detailsError: string | null;
}) {
  const pending = a.status === 'pending';
  const manual = a.approvalType === 'manual_task';
  const blocked = !!a.selfApprovalBlocked;
  const status = approvalStatus(a);
  const days = waitingDays(a.createdAt);
  const reportCount = a.reports?.length ?? 0;
  const params = Object.entries(a.workflowParameters ?? {});

  return (
    <>
      <tr
        className={cn('border-b border-gray-100 cursor-pointer hover:bg-gray-50', expanded && 'bg-gray-50 border-b-0')}
        onClick={onToggleExpand}
        aria-expanded={expanded}
      >
        {selectable && (
          <td className="w-8 py-2 pl-3 pr-0 align-top" onClick={(e) => e.stopPropagation()}>
            {isBulkCandidate(a) && (
              <input
                type="checkbox"
                className={cn('mt-1 w-4 h-4', blocked ? 'cursor-not-allowed' : 'cursor-pointer')}
                aria-label={`Select "${a.requestTitle}" to approve`}
                title={blocked ? SELF_APPROVAL_MESSAGE : undefined}
                checked={!blocked && selected}
                disabled={blocked}
                onChange={onToggleSelect}
              />
            )}
          </td>
        )}
        <td className="py-2 px-3 align-top">
          <TitleCell title={a.requestTitle} id={a.requestId} chevron={expanded}>
            {reportCount > 0 && (
              <span className={cn(chip, 'bg-indigo-50 text-indigo-700')} title="Earlier steps left a report for approvers">
                <FileText className="w-3 h-3" />
                {reportCount === 1 ? 'Report' : `${reportCount} reports`}
              </span>
            )}
            {manual && (
              <span className={cn(chip, 'bg-amber-50 text-amber-800')}>
                <ClipboardCheck className="w-3 h-3" />
                Manual task
              </span>
            )}
            {a.isOwnRequest && <span className={cn(chip, 'xl:hidden bg-gray-100 text-gray-600')}>Your request</span>}
          </TitleCell>
        </td>
        <td className="hidden lg:table-cell py-2 px-3 align-top text-sm text-gray-700">
          <p className="truncate" title={workflowLabel(a)}>{workflowLabel(a)}</p>
          <p className="truncate text-xs text-gray-500" title={approverLabel(a)}>{approverLabel(a)}</p>
        </td>
        <td className="hidden xl:table-cell py-2 px-3 align-top text-sm text-gray-700">
          <p className="truncate" title={a.requestedByEmail || a.requestedBy}>
            {a.isOwnRequest ? 'You' : a.requestedBy || a.requestedByEmail}
          </p>
        </td>
        <td className="py-2 px-3 align-top">
          <StatusBadge {...status} />
          {a.delegatedTo && <p className="mt-0.5 text-xs text-gray-500 truncate">→ {a.delegatedTo}</p>}
        </td>
        <td className="hidden sm:table-cell py-2 px-3 align-top text-sm whitespace-nowrap">
          {pending ? (
            <span title={`Requested ${formatDate(a.createdAt)}`}>
              {days >= AGING_DAYS ? (
                <span
                  className={cn(chip, 'text-xs', days >= STALE_DAYS ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-800')}
                  title={manual
                    ? "The request stays paused until someone marks this manual work done — it won't time out on its own."
                    : 'The request stays paused until someone decides.'}
                >
                  <AlertCircle className="w-3 h-3" />
                  {days}d
                </span>
              ) : (
                <span className="text-gray-600">{shortAge(a.createdAt)}</span>
              )}
            </span>
          ) : (
            <span className="text-gray-600" title={`Updated ${formatDate(a.updatedAt)}`}>{shortAge(a.updatedAt)} ago</span>
          )}
          {pending && a.dueAt && (
            <p className={cn('text-xs', isOverdue(a.dueAt) ? 'text-red-600 font-medium' : 'text-gray-500')} title={formatDate(a.dueAt)}>
              {isOverdue(a.dueAt) ? 'Overdue' : `Due ${shortDate(a.dueAt)}`}
            </p>
          )}
        </td>
        <td className="py-2 px-3 align-top" onClick={(e) => e.stopPropagation()}>
          <div className="flex justify-end gap-1.5">
            {pending ? (
              <>
                {/* A manual task is completed, not authorized — the same
                    approve/reject endpoints record the fact the poller waits
                    on, so only the wording changes. */}
                <span title={blocked ? SELF_APPROVAL_MESSAGE : undefined}>
                  <Button
                    size="sm"
                    onClick={() => onAction('approve')}
                    disabled={blocked}
                    className="h-7 px-2.5 text-xs bg-green-600 hover:bg-green-700 text-white"
                  >
                    <Check className="w-3.5 h-3.5 mr-1" />
                    {manual ? 'Mark done' : 'Approve'}
                  </Button>
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onAction('reject')}
                  className="h-7 px-2.5 text-xs border-red-300 text-red-700 hover:bg-red-50"
                >
                  <X className="w-3.5 h-3.5 mr-1" />
                  {manual ? "Can't complete" : 'Reject'}
                </Button>
              </>
            ) : (
              <Button size="sm" variant="ghost" onClick={onViewDetails} className="h-7 px-2 text-xs text-gray-600" title="View details">
                <Eye className="w-3.5 h-3.5 mr-1" />
                Details
              </Button>
            )}
          </div>
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-gray-100 bg-gray-50">
          <td colSpan={colSpan} className="px-3 pb-3 pt-0 sm:pl-14">
            <div className="grid gap-4 lg:grid-cols-2 text-sm">
              <div className="space-y-3 min-w-0">
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                  <dt className="text-gray-500">Requested by</dt>
                  <dd className="text-gray-800">{a.requestedBy}{a.requestedByEmail && a.requestedByEmail !== a.requestedBy ? ` (${a.requestedByEmail})` : ''}</dd>
                  <dt className="text-gray-500">{manual ? 'Task type' : 'Approver'}</dt>
                  <dd className="text-gray-800">{approverLabel(a)}</dd>
                  <dt className="text-gray-500">Requested</dt>
                  <dd className="text-gray-800">{formatDate(a.createdAt)}</dd>
                  {a.dueAt && (
                    <>
                      <dt className="text-gray-500">Due</dt>
                      <dd className={pending && isOverdue(a.dueAt) ? 'text-red-600 font-medium' : 'text-gray-800'}>
                        {pending && isOverdue(a.dueAt) ? 'Overdue — was due ' : ''}{formatDate(a.dueAt)}
                      </dd>
                    </>
                  )}
                  {!pending && (
                    <>
                      <dt className="text-gray-500">{status.label}</dt>
                      <dd className="text-gray-800">
                        {formatDate(a.updatedAt)}
                        {(a.approvedBy || a.rejectedBy) && ` by ${a.approvedBy || a.rejectedBy}`}
                      </dd>
                    </>
                  )}
                </dl>
                {/* What the assignee actually has to go do. Without this the
                    row is an unexplained "something is waiting on you". */}
                {manual && (
                  <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg">
                    <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide mb-1 flex items-center gap-1">
                      <ClipboardCheck className="w-3.5 h-3.5" />
                      What to do
                    </p>
                    <p className="text-sm text-gray-800 whitespace-pre-wrap">
                      {a.instructions?.trim() ||
                        'No instructions were recorded on this task — ask a platform admin before marking it done.'}
                    </p>
                  </div>
                )}
                {a.rejectionNote && (
                  <p className="p-2 bg-red-50 border border-red-200 rounded text-red-800">
                    <span className="font-medium">Rejection note:</span> {a.rejectionNote}
                  </p>
                )}
                {a.supersededNote && (
                  <p className="p-2 bg-orange-50 border border-orange-200 rounded text-orange-800 flex items-start gap-2">
                    <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                    <span>{a.supersededNote}</span>
                  </p>
                )}
                {pending && blocked && (
                  <p className="text-xs text-gray-500">{SELF_APPROVAL_MESSAGE} Rejecting it withdraws your request.</p>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" onClick={onViewDetails} className="h-8 text-xs">
                    <Eye className="w-3.5 h-3.5 mr-1.5" />
                    View full details
                  </Button>
                  {pending && canEdit && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => onAction('edit')}
                      className="h-8 text-xs border-orange-300 text-orange-700 hover:bg-orange-50"
                    >
                      <Pencil className="w-3.5 h-3.5 mr-1.5" />
                      Edit &amp; Restart
                    </Button>
                  )}
                </div>
                {detailsError && <p className="text-xs text-gray-500" role="status">{detailsError}</p>}
              </div>
              <div className="min-w-0">
                {params.length > 0 && (
                  <div className="p-3 bg-white border border-gray-200 rounded-lg">
                    <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2 flex items-center gap-1">
                      <Settings2 className="w-3.5 h-3.5" />
                      Workflow Parameters
                    </p>
                    <dl className="grid grid-cols-[minmax(0,10rem)_1fr] gap-x-4 gap-y-1">
                      {params.map(([k, v]) => (
                        <div key={k} className="contents">
                          <dt className="text-xs text-gray-500 font-medium truncate" title={k}>{k}</dt>
                          <dd className="text-xs text-gray-800 font-mono break-all">
                            {typeof v === 'object' ? JSON.stringify(v) : String(v ?? '')}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                )}
                {reportCount > 0 && <StepReportPanel reports={a.reports ?? []} />}
                {params.length === 0 && reportCount === 0 && (
                  <p className="text-xs italic text-gray-400">No parameters or reports on this request.</p>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function RequestRow({
  request: r,
  workflow,
  showRequester,
  onOpen,
  onDelete,
  deleting,
}: {
  request: Request;
  workflow: string;
  showRequester: boolean;
  onOpen: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const waiting = waitingOn(r);
  return (
    <tr className="border-b border-gray-100 cursor-pointer hover:bg-gray-50" onClick={onOpen}>
      <td className="py-2 px-3 align-top">
        <TitleCell title={r.title} id={r.id} />
      </td>
      <td className="hidden lg:table-cell py-2 px-3 align-top text-sm text-gray-700">
        <p className="truncate" title={workflow}>{workflow}</p>
      </td>
      {showRequester && (
        <td className="hidden xl:table-cell py-2 px-3 align-top text-sm text-gray-700">
          <p className="truncate" title={r.requester_email}>{r.requester_email || '—'}</p>
        </td>
      )}
      <td className="py-2 px-3 align-top">
        <StatusBadge label={r.status.replace(/_/g, ' ')} tone={requestStatusTone(r.status)} />
        {waiting && <p className="mt-0.5 text-xs text-gray-500 truncate" title={waiting}>{waiting}</p>}
      </td>
      <td className="hidden lg:table-cell py-2 px-3 align-top text-sm text-gray-600 whitespace-nowrap" title={formatDate(r.createdAt)}>
        {shortAge(r.createdAt)} ago
      </td>
      <td className="hidden sm:table-cell py-2 px-3 align-top text-sm text-gray-600 whitespace-nowrap" title={formatDate(r.updatedAt)}>
        {shortAge(r.updatedAt)} ago
      </td>
      <td className="py-2 px-3 align-top" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-end gap-1">
          <Button size="sm" variant="ghost" onClick={onOpen} className="h-7 w-7 p-0 text-gray-600" title="View details" aria-label={`View ${r.title}`}>
            <Eye className="w-4 h-4" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={onDelete}
            disabled={deleting}
            className="h-7 w-7 p-0 text-red-600 hover:text-red-700 hover:bg-red-50"
            title="Delete request"
            aria-label={`Delete ${r.title}`}
          >
            {deleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
          </Button>
        </div>
      </td>
    </tr>
  );
}
