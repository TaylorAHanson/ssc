import { useState } from 'react';
import { previewRevert, submitRevert } from '../../../services/metadataApi';
import type { ApprovalRouting, ChangeSet, RevertRequest } from '../../../types/metadata';
import { Notice } from '../../rbac/ui';
import { useMetadata } from './context';
import { changeSetCounts, formatTime } from './format';
import { ReviewDialog } from './ReviewDialog';
import { useReviewFlow } from './useReviewFlow';

/** Preview and submit putting back the values a change replaced. */
export function RevertDialog({ changeSet, onClose }: { changeSet: ChangeSet; onClose: () => void }) {
  const { bumpRefresh } = useMetadata();
  const [reason, setReason] = useState('');
  const [routing, setRouting] = useState<ApprovalRouting>('request');
  const flow = useReviewFlow<RevertRequest>({
    preview: (r) => previewRevert(changeSet.id, r),
    submit: (r) => submitRevert(changeSet.id, r),
  });

  const close = () => {
    if (flow.state.step === 'result' && flow.state.result.status !== 'rejected') bumpRefresh();
    onClose();
  };

  return (
    <ReviewDialog
      title="Undo a change"
      subtitle={`${changeSet.actor}, ${formatTime(changeSet.created_at)}`}
      flow={flow}
      reason={reason}
      onReasonChange={setReason}
      routing={routing}
      onRoutingChange={setRouting}
      buildRequest={(r, route) => ({ reason: r, on_approval_needed: route })}
      onClose={close}
      onPreview={() => flow.start({ reason, on_approval_needed: routing })}
      previewLabel="Preview the undo"
      reasonPlaceholder="Why are you undoing this change?"
    >
      {flow.state.step === 'idle' && (
        <Notice kind="info" title="What undoing does">
          It saves a new change that puts back the values this one replaced ({changeSetCounts(changeSet).join(', ') || 'no edits'}). If someone changed the same
          tags since, the preview shows it. Undoing a change to approval-gated tags needs approval again.
          {changeSet.reason && <span className="mt-1 block text-gray-700">Original reason: “{changeSet.reason}”</span>}
        </Notice>
      )}
    </ReviewDialog>
  );
}
