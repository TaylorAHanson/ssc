import { useCallback, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { api } from '../../services/api';
import type { RbacChangeSetResult, RbacChangeSetSummary, RbacSheetInfo } from '../../types/rbac';
import { Button } from '../ui/button';
import { Textarea } from '../ui/textarea';
import { DiffView } from './DiffView';
import { FindingList } from './FindingList';
import { ErrorBanner, Modal, Notice, SectionTitle, Spinner } from './ui';
import { useLoad } from './useLoad';
import { blockedPreviewOf, conflictOf, errorMessage, formatTime, sourceLabel } from './utils';

/** Previews the inverse of a change set and records it, with its own reason. */
export function RevertDialog({ changeSet, sheets, onClose, onDone, minReasonLength }: {
  changeSet: RbacChangeSetSummary;
  sheets: RbacSheetInfo[];
  onClose: () => void;
  onDone: (result: RbacChangeSetResult) => void;
  minReasonLength: number;
}) {
  const load = useCallback(() => api.validateRbacRevert(changeSet.id, ''), [changeSet.id]);
  const check = useLoad(load);
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);

  const preview = blockedPreviewOf(failure) ?? check.data ?? null;
  const conflict = conflictOf(failure) ?? conflictOf(check.error);
  const checkError = check.error && !conflict ? errorMessage(check.error, 'Could not preview the revert') : null;
  const submitError = failure && !conflict && !blockedPreviewOf(failure) ? errorMessage(failure, 'Revert failed') : null;
  const reasonShort = reason.trim().length < Math.max(minReasonLength, 1);
  const canSubmit = !!preview && !preview.blocked && !conflict && !submitting && !reasonShort;

  const submit = async () => {
    setSubmitting(true);
    setFailure(null);
    try {
      onDone(await api.revertRbacChangeSet(changeSet.id, reason.trim()));
    } catch (e) {
      setFailure(e);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      wide
      title="Revert this change set"
      subtitle={
        <>
          {sourceLabel(changeSet.source)} by {changeSet.actor || 'unknown'} on {formatTime(changeSet.created_at)}. Reverting
          records the opposite change as a new change set; this one stays in History.
        </>
      }
      onClose={onClose}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={!canSubmit} className="bg-red-600 hover:bg-red-700">
            {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {preview?.two_person_required ? 'File revert request' : 'Revert'}
          </Button>
        </>
      }
    >
      {check.loading && <Spinner label="Working out the revert…" />}
      {checkError && <ErrorBanner message={checkError} onRetry={check.reload} />}
      {conflict && (
        <Notice kind="error" title="Some of these rows have changed since">
          {conflict.message} Revert those rows by hand on the Rules tab instead.
        </Notice>
      )}
      {preview && !conflict && (
        <>
          {preview.two_person_required && (
            <Notice kind="info">
              The two-person rule is on for an environment this touches, so reverting files a request that another
              approver must approve.
            </Notice>
          )}
          <FindingList findings={preview.findings} sheet={sheets.length === 1 ? sheets[0] : null} denyHint="These block the revert." />
          <div>
            <SectionTitle>What the revert does</SectionTitle>
            <DiffView diffs={preview.diffs} sheets={sheets} />
          </div>
        </>
      )}
      <div>
        <label htmlFor="rbac-revert-reason" className="mb-1 block text-sm font-medium text-gray-700">
          Reason <span className="text-red-600">*</span>
        </label>
        <Textarea
          id="rbac-revert-reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
          placeholder="Why is this being undone?"
        />
        {minReasonLength > 1 && reasonShort && (
          <p className="mt-1 text-xs text-gray-500">At least {minReasonLength} characters.</p>
        )}
      </div>
      {submitError && <ErrorBanner message={submitError} />}
    </Modal>
  );
}
