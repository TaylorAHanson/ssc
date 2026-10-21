import { useState } from 'react';
import { ChevronDown, ChevronUp, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { previewChange, submitChange } from '../../../services/metadataApi';
import type { ChangeRequest } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { EnvBadge } from './badges';
import { useMetadata } from './context';
import { objectLabel, plural } from './format';
import { objectPath } from './paths';
import { ReviewDialog } from './ReviewDialog';
import { buildChangeRequest, editCount, hasEdits, stagedList, useStagingStore, type StagedTarget } from './staging';
import { TagChip } from './TagChips';
import { useReviewFlow } from './useReviewFlow';

/** "N unsaved changes · Discard · Review and save", pinned to the bottom while anything is unsaved. */
export function UnsavedBar() {
  const { bumpRefresh } = useMetadata();
  const store = useStagingStore();
  const edited = stagedList(store).filter(hasEdits);
  const edits = edited.reduce((n, t) => n + editCount(t), 0);
  const sourceEnvs = [...new Set(edited.flatMap((t) => (t.environment ? [t.environment] : [])))];
  const [open, setOpen] = useState(false);

  const flow = useReviewFlow<ChangeRequest>({
    preview: previewChange,
    submit: (request, key) => submitChange({ ...request, idempotency_key: key }, { idempotencyKey: key }),
  });

  if (!edited.length && flow.state.step === 'idle') return null;

  const closeReview = () => {
    const s = flow.state;
    if (s.step === 'result' && s.result.status !== 'rejected') {
      store.clear();
      bumpRefresh();
    }
    flow.reset();
  };

  return (
    <section
      aria-label="Unsaved changes"
      className="sticky bottom-0 z-20 -mx-1 rounded-t-lg border border-b-0 border-gray-300 bg-white shadow-[0_-4px_16px_rgba(0,0,0,0.08)]"
    >
      {edited.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 px-4 py-2.5">
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="inline-flex items-center gap-2 text-sm font-medium text-gray-900">
            {open ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
            {plural(edits, 'unsaved change')}
            {edited.length > 1 && <span className="font-normal text-gray-500">on {plural(edited.length, 'object')}</span>}
          </button>
          <div className="ml-auto flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={store.clear} className="text-gray-600">
              Discard
            </Button>
            <Button size="sm" onClick={() => flow.start(buildChangeRequest(store))}>
              Review and save
            </Button>
          </div>
        </div>
      )}

      {open && edited.length > 0 && (
        <div className="max-h-[50vh] space-y-4 overflow-y-auto border-t border-gray-200 px-4 py-3">
          <ul className="list-plain divide-y divide-gray-100 rounded-md border border-gray-200">
            {edited.map((t) => (
              <ChangeRow key={t.key} target={t} />
            ))}
          </ul>
        </div>
      )}

      {flow.state.step !== 'idle' && (
        <ReviewDialog
          title="Review and save"
          subtitle="Check what will change, and whether anything needs someone else’s approval."
          flow={flow}
          reason={store.reason}
          onReasonChange={store.setReason}
          routing={store.routing}
          onRoutingChange={store.setRouting}
          buildRequest={(reason, routing) => ({ ...buildChangeRequest(useStagingStore.getState()), reason, on_approval_needed: routing })}
          onClose={closeReview}
          onConflictReload={(conflicts) => {
            useStagingStore.getState().rebase(conflicts);
            flow.start(buildChangeRequest(useStagingStore.getState()));
          }}
          environments={
            sourceEnvs.length
              ? {
                  value: store.environments,
                  exclude: sourceEnvs.length === 1 ? sourceEnvs : [],
                  onChange: (envs) => {
                    store.setEnvironments(envs);
                    flow.start(buildChangeRequest(useStagingStore.getState()));
                  },
                }
              : undefined
          }
        />
      )}
    </section>
  );
}

function ChangeRow({ target }: { target: StagedTarget }) {
  const remove = useStagingStore((s) => s.remove);
  const label = objectLabel(target.name, target.column);
  return (
    <li className="flex flex-wrap items-center gap-2 px-3 py-2">
      {target.declared ? (
        <span className="font-mono text-xs font-medium text-gray-900">{label}</span>
      ) : (
        <Link to={objectPath(target.name, target.column)} className="font-mono text-xs font-medium text-gray-900 hover:text-primary hover:underline">
          {label}
        </Link>
      )}
      <EnvBadge env={target.environment} />
      {Object.entries(target.set).map(([k, v]) => (
        <TagChip key={k} tagKey={k} value={v} tone={k in target.baseTags ? 'changed' : 'added'} />
      ))}
      {target.unset.map((k) => (
        <TagChip key={k} tagKey={k} value={target.baseTags[k]} tone="removed" />
      ))}
      {target.comment && <span className="text-xs text-amber-800">{target.comment.value === null ? 'Description cleared' : 'Description changed'}</span>}
      <button
        type="button"
        onClick={() => remove(target.key)}
        className="ml-auto inline-flex h-7 w-7 items-center justify-center rounded text-gray-500 hover:bg-gray-100 hover:text-red-700"
        aria-label={`Discard the changes to ${label}`}
        title="Discard these changes"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </li>
  );
}