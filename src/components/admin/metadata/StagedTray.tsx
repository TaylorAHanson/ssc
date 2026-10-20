import { useId, useState, type FormEvent } from 'react';
import { ChevronDown, ChevronUp, PencilLine, Trash2, X } from 'lucide-react';
import { previewChange, submitChange } from '../../../services/metadataApi';
import type { ChangeRequest } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Badge } from '../../rbac/ui';
import { EnvBadge } from './badges';
import { useMetadata } from './context';
import { TYPE_LABELS, objectLabel, plural } from './format';
import { ReviewDialog } from './ReviewDialog';
import { buildChangeRequest, editCount, effectiveTags, hasEdits, stagedList, useStagingStore, type StagedTarget } from './staging';
import { AddTagForm, TargetEditor } from './TagEditor';
import { TagChip } from './TagChips';
import { useReviewFlow } from './useReviewFlow';

/** Staged edits across one or many objects, kept until they're submitted or cleared. */
export function StagedTray() {
  const { bumpRefresh } = useMetadata();
  const store = useStagingStore();
  const targets = stagedList(store);
  const edited = targets.filter(hasEdits);
  const edits = edited.reduce((n, t) => n + editCount(t), 0);
  const [expanded, setExpanded] = useState<string | null>(null);

  const flow = useReviewFlow<ChangeRequest>({
    preview: previewChange,
    submit: (request, key) => submitChange({ ...request, idempotency_key: key }, { idempotencyKey: key }),
  });

  if (!targets.length) return null;

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
      aria-label="Staged edits"
      className="sticky bottom-0 z-20 -mx-1 rounded-t-lg border border-b-0 border-gray-300 bg-white shadow-[0_-4px_16px_rgba(0,0,0,0.08)]"
    >
      <div className="flex flex-wrap items-center gap-3 px-4 py-2.5">
        <button
          type="button"
          onClick={() => store.setTrayOpen(!store.trayOpen)}
          aria-expanded={store.trayOpen}
          className="inline-flex items-center gap-2 text-sm font-medium text-gray-900"
        >
          {store.trayOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
          Staged edits
          <Badge tone="indigo">{plural(targets.length, 'object')}</Badge>
          {edits > 0 && <Badge tone="amber">{plural(edits, 'edit')}</Badge>}
        </button>
        <span className="text-xs text-gray-500">Nothing is saved until you review and submit.</span>
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={store.clear} className="gap-1 text-gray-600">
            <Trash2 className="h-3.5 w-3.5" /> Discard all
          </Button>
          <Button size="sm" disabled={!edited.length} onClick={() => flow.start(buildChangeRequest(store))}>
            Review and submit
          </Button>
        </div>
      </div>

      {store.trayOpen && (
        <div className="max-h-[55vh] space-y-4 overflow-y-auto border-t border-gray-200 px-4 py-3">
          {targets.length > 1 && <BulkEditor targets={targets} />}
          <ul className="divide-y divide-gray-100 rounded-md border border-gray-200">
            {targets.map((t) => (
              <TargetRow
                key={t.key}
                target={t}
                expanded={expanded === t.key || targets.length === 1}
                onToggle={() => setExpanded(expanded === t.key ? null : t.key)}
              />
            ))}
          </ul>
          <EnvironmentChoice targets={targets} />
        </div>
      )}

      {flow.state.step !== 'idle' && (
        <ReviewDialog
          title="Review staged edits"
          subtitle="Check what will be saved and what needs approval."
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
        />
      )}
    </section>
  );
}

function TargetRow({ target, expanded, onToggle }: { target: StagedTarget; expanded: boolean; onToggle: () => void }) {
  const remove = useStagingStore((s) => s.remove);
  const { openObject } = useMetadata();
  const tags = effectiveTags(target);
  const changedKeys = [...Object.keys(target.set), ...target.unset];
  return (
    <li className="px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => (target.declared ? onToggle() : openObject(target.name, target.column))}
          className="font-mono text-xs font-medium text-gray-900 hover:text-primary hover:underline"
        >
          {objectLabel(target.name, target.column)}
        </button>
        <span className="text-xs text-gray-500">{TYPE_LABELS[target.securableType]}</span>
        <EnvBadge env={target.environment} />
        {target.declared && <Badge tone="blue">New declaration</Badge>}
        {!target.managed && !target.declared && (
          <Badge tone="gray" title="Saving takes this object under management with its current tags, so none are removed.">
            Starts managing
          </Badge>
        )}
        {!hasEdits(target) && <span className="text-xs text-gray-400">{target.declared ? 'Add a tag or description' : 'No edits yet'}</span>}
        <span className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={expanded}
            className="inline-flex h-7 items-center gap-1 rounded px-2 text-xs text-gray-600 hover:bg-gray-100"
          >
            <PencilLine className="h-3.5 w-3.5" /> {expanded ? 'Done' : 'Edit'}
          </button>
          <button
            type="button"
            onClick={() => remove(target.key)}
            className="inline-flex h-7 w-7 items-center justify-center rounded text-gray-500 hover:bg-gray-100 hover:text-red-700"
            aria-label={`Remove ${objectLabel(target.name, target.column)} from staged edits`}
            title="Remove from staged edits"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </span>
      </div>
      {!expanded && (changedKeys.length > 0 || target.comment) && (
        <div className="mt-1 flex flex-wrap gap-1">
          {Object.entries(target.set).map(([k, v]) => (
            <TagChip key={k} tagKey={k} value={v} tone={k in target.baseTags ? 'changed' : 'added'} />
          ))}
          {target.unset.map((k) => (
            <TagChip key={k} tagKey={k} value={target.baseTags[k]} tone="removed" />
          ))}
          {target.comment && (
            <span className="text-xs text-amber-800">{target.comment.value === null ? 'Description cleared' : 'Description changed'}</span>
          )}
          <span className="sr-only">Resulting tags: {Object.keys(tags).join(', ') || 'none'}</span>
        </div>
      )}
      {expanded && (
        <div className="mt-2 rounded-md bg-gray-50 p-3">
          <TargetEditor target={target} />
        </div>
      )}
    </li>
  );
}

function BulkEditor({ targets }: { targets: StagedTarget[] }) {
  const { config } = useMetadata();
  const setTag = useStagingStore((s) => s.setTag);
  const unsetTag = useStagingStore((s) => s.unsetTag);
  const setComment = useStagingStore((s) => s.setComment);
  const [removeKey, setRemoveKey] = useState('');
  const [comment, setCommentText] = useState('');
  const keys = targets.map((t) => t.key);
  const ids = useId();
  const existingKeys = [...new Set(targets.flatMap((t) => Object.keys(effectiveTags(t))))].filter((k) => !config.exempt_keys.includes(k)).sort();

  const remove = (e: FormEvent) => {
    e.preventDefault();
    if (!removeKey.trim()) return;
    unsetTag(keys, removeKey.trim());
    setRemoveKey('');
  };

  return (
    <div className="space-y-3 rounded-md border border-indigo-100 bg-indigo-50/40 p-3">
      <p className="text-sm font-medium text-gray-900">Edit all {targets.length} staged objects at once</p>
      <div className="space-y-1">
        <span className="text-xs font-medium text-gray-600">Add or change a tag on every object</span>
        <AddTagForm compact submitLabel="Apply to all" onAdd={(k, v) => setTag(keys, k, v)} />
      </div>
      <form onSubmit={remove} className="flex flex-wrap items-center gap-2">
        <label htmlFor={`${ids}-remove`} className="text-xs font-medium text-gray-600">
          Remove a tag from every object
        </label>
        <select
          id={`${ids}-remove`}
          value={removeKey}
          onChange={(e) => setRemoveKey(e.target.value)}
          className="h-8 rounded-md border border-gray-300 bg-white px-2 text-xs"
        >
          <option value="">Choose a key</option>
          {existingKeys.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
        <Button type="submit" size="sm" variant="outline" className="h-8 text-xs" disabled={!removeKey}>
          Remove from all
        </Button>
      </form>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={`${ids}-comment`} className="text-xs font-medium text-gray-600">
          Description for every object
        </label>
        <Input
          id={`${ids}-comment`}
          value={comment}
          onChange={(e) => setCommentText(e.target.value)}
          placeholder="New description"
          className="h-8 w-64 text-xs"
        />
        <Button size="sm" variant="outline" className="h-8 text-xs" disabled={!comment.trim()} onClick={() => setComment(keys, comment)}>
          Set on all
        </Button>
        <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={() => setComment(keys, null)}>
          Clear on all
        </Button>
      </div>
    </div>
  );
}

function EnvironmentChoice({ targets }: { targets: StagedTarget[] }) {
  const { config } = useMetadata();
  const environments = useStagingStore((s) => s.environments);
  const setEnvironments = useStagingStore((s) => s.setEnvironments);
  if (config.environments.length < 2) return null;
  const withEnv = targets.some((t) => t.environment);
  return (
    <fieldset className="space-y-1">
      <legend className="text-sm font-medium text-gray-900">Also apply to</legend>
      <p className="text-xs text-gray-500">
        Copies these edits to the same object in each environment you tick, for example from <code>sales_dev</code> to{' '}
        <code>sales_prd</code>. Objects already in that environment are left as they are.
        {!withEnv && ' None of the staged objects belong to an environment, so there’s nothing to copy.'}
      </p>
      <div className="flex flex-wrap gap-4">
        {config.environments.map((env) => (
          <label key={env.key} className="inline-flex cursor-pointer items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              disabled={!withEnv}
              checked={environments.includes(env.key)}
              onChange={(e) => setEnvironments(e.target.checked ? [...environments, env.key] : environments.filter((k) => k !== env.key))}
              className="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
            />
            <EnvBadge env={env.key} />
            {env.approval === 'all' && <span className="text-xs text-gray-500">(every edit needs approval)</span>}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
