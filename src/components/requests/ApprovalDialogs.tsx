import { useEffect, useState } from 'react';
import { AlertCircle, Check, Pencil, X } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Textarea } from '../ui/textarea';
import { StepReportPanel } from '../StepReportPanel';
import { useRequestStore } from '../../stores/requestStore';
import { api, type BulkApproveResult } from '../../services/api';
import { BULK_APPROVE_MAX, workflowLabel, type InboxApproval } from './rows';

export type ApprovalDialogAction = 'approve' | 'reject' | 'edit';

function useEscape(onEscape: () => void, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onEscape();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onEscape, enabled]);
}

const stringifyParams = (params: Record<string, unknown> | undefined) =>
  Object.fromEntries(
    Object.entries(params ?? {}).map(([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : String(v ?? '')]),
  );

/** Confirm one approve / reject / Edit & Restart. Stays open with the error if the call fails. */
export function ApprovalActionDialog({
  approval,
  action,
  onClose,
}: {
  approval: InboxApproval;
  action: ApprovalDialogAction;
  onClose: () => void;
}) {
  const processApproval = useRequestStore((state) => state.processApproval);
  const fetchApprovals = useRequestStore((state) => state.fetchApprovals);
  const [note, setNote] = useState('');
  const [editParams, setEditParams] = useState<Record<string, string>>(() =>
    action === 'edit' ? stringifyParams(approval.workflowParameters) : {},
  );
  const [isProcessing, setIsProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isManualTask = approval.approvalType === 'manual_task';
  const reports = action === 'approve' ? approval.reports ?? [] : [];

  useEscape(onClose, !isProcessing);

  const submit = async () => {
    if (action === 'reject' && !note.trim()) return;
    setIsProcessing(true);
    setError(null);
    try {
      if (action === 'edit') {
        // Coerce string values back to their original types where possible
        const coerced: Record<string, unknown> = {};
        Object.entries(editParams).forEach(([k, v]) => {
          try { coerced[k] = JSON.parse(v); } catch { coerced[k] = v; }
        });
        await api.editRequestParameters(approval.requestId, coerced, note || undefined);
        await fetchApprovals();
      } else {
        await processApproval({
          approvalId: approval.id,
          action,
          note: action === 'reject' ? note : undefined,
        });
      }
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That did not go through. Try again.');
    } finally {
      setIsProcessing(false);
    }
  };

  const title =
    action === 'approve' ? (isManualTask ? 'Mark Task Done' : 'Approve Request')
      : action === 'reject' ? (isManualTask ? "Can't Complete Task" : 'Reject Request')
        : 'Edit Parameters & Restart';

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => !isProcessing && onClose()}>
      <Card
        className={`w-full ${reports.length > 0 ? 'max-w-2xl' : 'max-w-md'} max-h-[90vh] flex flex-col`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="approval-action-title"
        onClick={(e) => e.stopPropagation()}
      >
        <CardHeader className="shrink-0 pb-3">
          <CardTitle id="approval-action-title" className="text-lg flex items-center gap-2">
            {action === 'edit' && <Pencil className="w-4 h-4 text-orange-500" />}
            {title}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 overflow-y-auto">
          <div className="text-sm text-gray-600">
            <p className="font-medium text-gray-900">{approval.requestTitle}</p>
            <p className="text-xs text-gray-500">
              {workflowLabel(approval)} · requested by {approval.requestedByEmail || approval.requestedBy}
            </p>
          </div>

          {/* Approving from the list must not skip what earlier steps reported. */}
          {reports.length > 0 && <StepReportPanel reports={reports} />}

          {action === 'edit' && (
            <div className="space-y-3">
              <p className="text-xs text-gray-500 flex items-start gap-1.5 bg-orange-50 border border-orange-200 rounded p-2">
                <AlertCircle className="w-4 h-4 text-orange-500 shrink-0 mt-0.5" />
                Editing parameters will supersede the current approval and restart the Terraform planning phase with the new values.
              </p>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Parameters</p>
              <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
                {Object.keys(editParams).length === 0 ? (
                  <p className="text-sm text-gray-400 italic">No editable parameters available.</p>
                ) : (
                  Object.entries(editParams).map(([key, value]) => (
                    <div key={key} className="flex items-center gap-2">
                      <label className="text-xs font-mono text-gray-600 w-40 shrink-0 truncate" title={key}>{key}</label>
                      <input
                        type="text"
                        value={value}
                        onChange={(e) => setEditParams((prev) => ({ ...prev, [key]: e.target.value }))}
                        className="flex-1 text-xs font-mono border border-gray-300 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-orange-400"
                      />
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {action === 'approve' && isManualTask && approval.instructions?.trim() && (
            <div className="text-sm text-gray-700 bg-amber-50 border border-amber-200 rounded p-3">
              <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide mb-1">Confirm you completed</p>
              <p className="whitespace-pre-wrap">{approval.instructions}</p>
            </div>
          )}

          {action === 'reject' && (
            <div>
              <label htmlFor="approval-reject-note" className="block text-sm font-medium text-gray-700 mb-2">
                {isManualTask ? 'Why it can\u2019t be completed' : 'Rejection Note'} <span className="text-red-500">*</span>
              </label>
              <Textarea
                id="approval-reject-note"
                autoFocus
                placeholder={
                  isManualTask
                    ? 'Explain what blocked the task so the requester knows what happened...'
                    : 'Please provide a reason for rejection...'
                }
                value={note}
                onChange={(e) => setNote(e.target.value)}
                className="w-full min-h-[100px]"
              />
              {approval.selfApprovalBlocked && (
                <p className="mt-1 text-xs text-gray-500">This is your own request, so rejecting it withdraws it.</p>
              )}
            </div>
          )}

          {action === 'edit' && (
            <div>
              <label htmlFor="approval-edit-note" className="block text-sm font-medium text-gray-700 mb-2">
                Note <span className="text-gray-400 font-normal">(optional)</span>
              </label>
              <Textarea
                id="approval-edit-note"
                placeholder="Reason for editing parameters..."
                value={note}
                onChange={(e) => setNote(e.target.value)}
                className="w-full min-h-[80px]"
              />
            </div>
          )}

          {error && (
            <p role="alert" className="rounded bg-red-50 border border-red-200 p-2 text-sm text-red-700">{error}</p>
          )}

          <div className="flex gap-2 pt-2">
            <Button
              onClick={submit}
              autoFocus={action === 'approve'}
              disabled={
                isProcessing ||
                (action === 'reject' && !note.trim()) ||
                (action === 'edit' && Object.keys(editParams).length === 0)
              }
              className={
                action === 'approve' ? 'bg-green-600 hover:bg-green-700'
                  : action === 'reject' ? 'bg-red-600 hover:bg-red-700'
                    : 'bg-orange-500 hover:bg-orange-600'
              }
            >
              {isProcessing ? 'Processing...'
                : action === 'approve' ? (isManualTask ? 'Confirm Done' : 'Confirm Approval')
                  : action === 'reject' ? (isManualTask ? "Confirm Can't Complete" : 'Confirm Rejection')
                    : 'Apply & Restart'}
            </Button>
            <Button variant="outline" onClick={onClose} disabled={isProcessing}>
              Cancel
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/** Confirm, then report per-item results for, a bulk approval. */
export function BulkApproveDialog({
  selected,
  onClose,
  onDone,
}: {
  selected: InboxApproval[];
  /** Closed without sending, or after the results were read. */
  onClose: (sent: boolean) => void;
  /** Refetch after the approvals went out. */
  onDone: () => Promise<void>;
}) {
  const [stage, setStage] = useState<'confirm' | 'results'>('confirm');
  const [comment, setComment] = useState('');
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<BulkApproveResult[]>([]);
  // Titles of the requests sent, so results still read well after the list refreshes.
  const [titles, setTitles] = useState<Record<string, string>>({});
  const tooMany = selected.length > BULK_APPROVE_MAX;
  const close = () => !processing && onClose(stage === 'results');

  useEscape(close, !processing);

  const submit = async () => {
    const requestIds = [...new Set(selected.map((a) => a.requestId))];
    if (requestIds.length === 0) return;
    setProcessing(true);
    setError(null);
    setTitles(Object.fromEntries(selected.map((a) => [a.requestId, a.requestTitle])));
    try {
      const { results: res } = await api.bulkApproveRequests(requestIds, comment.trim() || undefined);
      setResults(res);
      setStage('results');
      await onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The approvals could not be sent.');
    } finally {
      setProcessing(false);
    }
  };

  const approved = results.filter((r) => r.ok);
  const failed = results.filter((r) => !r.ok);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={close}>
      <Card
        className="w-full max-w-lg"
        role="dialog"
        aria-modal="true"
        aria-labelledby="bulk-approve-title"
        onClick={(e) => e.stopPropagation()}
      >
        <CardHeader className="pb-3">
          <CardTitle id="bulk-approve-title" className="text-lg">
            {stage === 'confirm'
              ? `Approve ${selected.length} request${selected.length === 1 ? '' : 's'}`
              : 'Bulk approval results'}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {stage === 'confirm' ? (
            <>
              <p className="text-sm text-gray-600">
                Each request is approved on its own, exactly as if you approved it individually. If one can't
                be approved, the others still go through.
              </p>
              <ul className="max-h-64 overflow-y-auto divide-y divide-gray-100 rounded border border-gray-200">
                {selected.map((a) => (
                  <li key={a.id} className="px-3 py-2 text-sm">
                    <p className="font-medium text-gray-900">{a.requestTitle}</p>
                    <p className="text-xs text-gray-500">
                      {workflowLabel(a)} · requested by {a.requestedByEmail || a.requestedBy}
                      {(a.reports?.length ?? 0) > 0 && ' · has a report'}
                    </p>
                  </li>
                ))}
              </ul>
              <div>
                <label htmlFor="bulk-approve-comment" className="block text-sm font-medium text-gray-700 mb-2">
                  Comment <span className="text-gray-400 font-normal">(optional)</span>
                </label>
                <Textarea
                  id="bulk-approve-comment"
                  autoFocus
                  placeholder="Recorded with each approval..."
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  maxLength={2000}
                  className="w-full min-h-[80px]"
                />
              </div>
              {tooMany && (
                <p className="text-xs text-red-600" role="status">Select at most {BULK_APPROVE_MAX} at a time.</p>
              )}
              {error && (
                <p role="alert" className="rounded bg-red-50 border border-red-200 p-2 text-sm text-red-700">{error}</p>
              )}
              <div className="flex gap-2 pt-2">
                <Button
                  onClick={submit}
                  disabled={processing || selected.length === 0 || tooMany}
                  className="bg-green-600 hover:bg-green-700"
                >
                  {processing ? 'Approving...' : 'Confirm Approval'}
                </Button>
                <Button variant="outline" onClick={close} disabled={processing}>Cancel</Button>
              </div>
            </>
          ) : (
            <>
              <p className="text-sm text-gray-700" role="status">
                Approved {approved.length} of {results.length}.
                {failed.length > 0 && ` ${failed.length} couldn't be approved.`}
              </p>
              <ul className="max-h-72 overflow-y-auto divide-y divide-gray-100 rounded border border-gray-200">
                {results.map((r) => (
                  <li key={r.request_id} className="flex items-start gap-2 px-3 py-2 text-sm">
                    {r.ok ? (
                      <Check className="w-4 h-4 mt-0.5 shrink-0 text-green-600" aria-hidden="true" />
                    ) : (
                      <X className="w-4 h-4 mt-0.5 shrink-0 text-red-600" aria-hidden="true" />
                    )}
                    <div>
                      <p className="font-medium text-gray-900">{titles[r.request_id] || r.request_id}</p>
                      <p className={`text-xs ${r.ok ? 'text-green-700' : 'text-red-700'}`}>
                        {r.ok ? 'Approved' : `Not approved: ${r.detail}`}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
              <div className="flex pt-2">
                <Button onClick={close} autoFocus>Done</Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
