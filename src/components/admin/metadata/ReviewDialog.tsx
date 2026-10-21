import { useId, type ReactNode } from 'react';
import { Button } from '../../ui/button';
import { Textarea } from '../../ui/textarea';
import type { ApprovalRouting, ChangePreview, VersionConflictItem } from '../../../types/metadata';
import { ErrorBanner, Modal, Notice, Spinner } from '../../rbac/ui';
import { useMetadata } from './context';
import { EnvironmentChoice, type EnvironmentChoiceProps } from './EnvironmentChoice';
import { PreviewView } from './PreviewView';
import { ConflictPanel, ResultView } from './ResultView';
import { RoutingChoice } from './RoutingChoice';
import { plural, realEditCount, reasonHint } from './format';
import type { ReviewFlow } from './useReviewFlow';

interface ReviewDialogProps<R> {
  title: string;
  subtitle?: ReactNode;
  flow: ReviewFlow<R>;
  reason: string;
  onReasonChange: (reason: string) => void;
  /** Omit for actions without approval routing (import). */
  routing?: ApprovalRouting;
  onRoutingChange?: (routing: ApprovalRouting) => void;
  /** The request to submit, with the reason and routing as currently entered. */
  buildRequest: (reason: string, routing: ApprovalRouting) => R;
  onClose: () => void;
  onConflictReload?: (conflicts: VersionConflictItem[]) => void;
  /** Shown above the preview, e.g. options that change what's previewed. */
  children?: ReactNode;
  reasonPlaceholder?: string;
  /** When given, the dialog opens on `children` (options) with a Preview button. */
  onPreview?: () => void;
  previewDisabled?: boolean;
  previewLabel?: string;
  /** The options changed after the preview; submitting is held until it's re-run. */
  stale?: boolean;
  /** Offers "Also make these changes in"; `onChange` should re-run the preview when one is showing. */
  environments?: EnvironmentChoiceProps;
}

function submitLabel(p: ChangePreview, routing: ApprovalRouting | undefined): string {
  const edits = realEditCount(p);
  if (!p.approval_count) return edits ? `Save ${plural(edits, 'edit')}` : p.direct_count ? 'Save' : 'Submit';
  if (routing === 'reject') return 'Submit (nothing saved if approval is needed)';
  if (routing === 'split' && edits) return `Save ${edits}, send ${p.approval_count} for approval`;
  return 'Send for approval';
}

/** Preview → reason and routing → submit → result, with 409 and 422 handled in place. */
export function ReviewDialog<R>({
  title,
  subtitle,
  flow,
  reason,
  onReasonChange,
  routing,
  onRoutingChange,
  buildRequest,
  onClose,
  onConflictReload,
  children,
  reasonPlaceholder,
  onPreview,
  previewDisabled,
  previewLabel = 'Preview',
  stale,
  environments,
}: ReviewDialogProps<R>) {
  const { config } = useMetadata();
  const reasonId = useId();
  const { state } = flow;
  const reasonOk = reason.trim().length >= config.min_reason_length;
  const effectiveRouting = routing ?? 'request';

  const reasonField = (
    <div>
      <label htmlFor={reasonId} className="mb-1 block text-sm font-medium text-gray-800">
        Reason <span className="font-normal text-gray-500">({reasonHint(config.min_reason_length)})</span>
      </label>
      <Textarea
        id={reasonId}
        data-autofocus
        rows={2}
        autoResize
        value={reason}
        onChange={(e) => onReasonChange(e.target.value)}
        placeholder={reasonPlaceholder ?? 'Why is this change needed? Approvers and the change history show this.'}
        className="max-h-40 text-sm"
        aria-invalid={!reasonOk && reason.length > 0}
      />
      {!reasonOk && reason.length > 0 && (
        <p className="mt-1 text-xs text-gray-500">{config.min_reason_length - reason.trim().length} more characters needed.</p>
      )}
    </div>
  );

  let body: ReactNode;
  let footer: ReactNode;
  const cancel = <Button variant="outline" onClick={onClose}>Cancel</Button>;
  switch (state.step) {
    case 'idle':
      body = onPreview ? null : <Spinner label="Working out what this would change…" />;
      footer = (
        <>
          {cancel}
          {onPreview && (
            <Button onClick={onPreview} disabled={previewDisabled}>
              {previewLabel}
            </Button>
          )}
        </>
      );
      break;
    case 'previewing':
      body = <Spinner label="Working out what this would change…" />;
      footer = cancel;
      break;
    case 'preview': {
      const p = state.preview;
      const canSubmit = !p.blocked && reasonOk && !state.submitting && p.items.length > 0 && !stale;
      body = (
        <>
          {stale && <Notice kind="warn">You changed the options. Preview again to see what they do before submitting.</Notice>}
          <PreviewView preview={p} reasonOk={reasonOk} />
          {state.error && <ErrorBanner message={state.error} />}
          {!p.blocked && p.items.length > 0 && (
            <>
              {p.approval_count > 0 && onRoutingChange && <RoutingChoice value={effectiveRouting} onChange={onRoutingChange} preview={p} />}
              {reasonField}
            </>
          )}
        </>
      );
      footer = (
        <>
          <Button variant="outline" onClick={onClose} disabled={state.submitting}>
            {p.items.length ? 'Back' : 'Close'}
          </Button>
          {stale && onPreview ? (
            <Button onClick={onPreview} disabled={previewDisabled}>
              Preview again
            </Button>
          ) : (
            p.items.length > 0 && (
              <Button onClick={() => flow.submit(buildRequest(reason.trim(), effectiveRouting))} disabled={!canSubmit}>
                {state.submitting ? 'Submitting…' : state.error ? 'Try again' : submitLabel(p, routing)}
              </Button>
            )
          )}
        </>
      );
      break;
    }
    case 'result':
      body = <ResultView result={state.result} onLeave={onClose} />;
      footer = <Button onClick={onClose}>Done</Button>;
      break;
    case 'conflict':
      body = <ConflictPanel message={state.message} conflicts={state.conflicts} />;
      footer = (
        <>
          {cancel}
          {onConflictReload && <Button onClick={() => onConflictReload(state.conflicts)}>Reload and review again</Button>}
          {!onConflictReload && onPreview && <Button onClick={onPreview}>Preview again</Button>}
        </>
      );
      break;
    case 'blocked':
      body = (
        <>
          <Notice kind="error" title="This change was blocked">{state.message}</Notice>
          <PreviewView preview={state.preview} reasonOk={reasonOk} />
        </>
      );
      footer = <Button variant="outline" onClick={onClose}>Back</Button>;
      break;
    case 'error':
      body = <ErrorBanner message={state.message} onRetry={() => flow.start(state.request)} />;
      footer = <Button variant="outline" onClick={onClose}>Close</Button>;
      break;
  }

  const showEnvironments = environments && (state.step === 'idle' || state.step === 'previewing' || state.step === 'preview');
  const busy = state.step === 'previewing' || (state.step === 'preview' && state.submitting);

  return (
    <Modal title={title} subtitle={subtitle} onClose={onClose} footer={footer} wide>
      {state.step !== 'result' && children}
      {showEnvironments && <EnvironmentChoice {...environments} disabled={environments.disabled || busy} />}
      {body}
    </Modal>
  );
}
