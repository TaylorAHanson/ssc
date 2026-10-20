import { useId, useState, type FormEvent } from 'react';
import { Plus, Undo2 } from 'lucide-react';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Textarea } from '../../ui/textarea';
import { useMetadata } from './context';
import { tagProblem } from './format';
import { effectiveComment, useStagingStore, type StagedTarget } from './staging';
import { TagChip } from './TagChips';

/** Key + value inputs with suggestions; a blank value makes a key-only tag. */
export function AddTagForm({ onAdd, submitLabel = 'Add', compact }: {
  onAdd: (key: string, value: string | null) => void;
  submitLabel?: string;
  compact?: boolean;
}) {
  const { config, keys } = useMetadata();
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const k = key.trim();
  const policy = config.key_policies.find((p) => p.key === k);
  const info = keys.find((i) => i.key === k);
  const valueOptions = policy?.allowed_values.length ? policy.allowed_values : info?.values.map((v) => v.value) ?? [];

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const v = value.trim() || null;
    const problem = tagProblem(k, v, config, keys);
    if (problem) {
      setError(problem);
      return;
    }
    onAdd(k, v);
    setKey('');
    setValue('');
    setError(null);
  };

  return (
    <form onSubmit={submit} className="space-y-1" aria-label="Add a tag">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={key}
          onChange={(e) => {
            setKey(e.target.value);
            setError(null);
          }}
          list={`${id}-keys`}
          placeholder="Tag key"
          aria-label="Tag key"
          className={compact ? 'h-8 w-40 text-xs' : 'h-9 w-44'}
        />
        <datalist id={`${id}-keys`}>
          {keys.filter((i) => !i.exempt).map((i) => (
            <option key={i.key} value={i.key}>
              {i.policy?.description ?? `${i.count} objects`}
            </option>
          ))}
        </datalist>
        <span className="text-gray-400" aria-hidden="true">=</span>
        <Input
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setError(null);
          }}
          list={`${id}-values`}
          placeholder={policy?.allowed_values.length ? 'Choose a value' : 'Value (blank = key only)'}
          aria-label="Tag value"
          className={compact ? 'h-8 w-48 text-xs' : 'h-9 w-52'}
        />
        <datalist id={`${id}-values`}>
          {valueOptions.map((v) => (
            <option key={v} value={v} />
          ))}
        </datalist>
        <Button type="submit" size="sm" variant="outline" className={compact ? 'h-8 gap-1 text-xs' : 'gap-1'}>
          <Plus className="h-3.5 w-3.5" /> {submitLabel}
        </Button>
      </div>
      {policy && (policy.sensitive || policy.description) && !error && (
        <p className="text-xs text-gray-500">
          {policy.description}
          {policy.sensitive && ' Changing this key needs approval unless you’re an owner allowed to set it.'}
        </p>
      )}
      {error && (
        <p className="text-xs text-red-700" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

/** Edit one staged object's tags and description; every edit goes to the staging store. */
export function TargetEditor({ target }: { target: StagedTarget }) {
  const { config } = useMetadata();
  const setTag = useStagingStore((s) => s.setTag);
  const unsetTag = useStagingStore((s) => s.unsetTag);
  const setComment = useStagingStore((s) => s.setComment);
  const undoField = useStagingStore((s) => s.undoField);
  const keys = [...new Set([...Object.keys(target.baseTags), ...Object.keys(target.set)])].sort();
  const comment = effectiveComment(target);
  const commentId = useId();

  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1 text-xs font-medium text-gray-600">Tags</div>
        <ul className="flex flex-wrap gap-1.5" aria-label="Tags">
          {keys.length === 0 && <li className="text-xs text-gray-400">No tags yet.</li>}
          {keys.map((k) => {
            const exempt = config.exempt_keys.includes(k);
            const removed = target.unset.includes(k);
            const added = k in target.set && !(k in target.baseTags);
            const changed = k in target.set && k in target.baseTags;
            const value = k in target.set ? target.set[k] : target.baseTags[k];
            return (
              <li key={k} className="inline-flex items-center gap-1">
                <TagChip
                  tagKey={k}
                  value={value}
                  tone={exempt ? 'exempt' : removed ? 'removed' : added ? 'added' : changed ? 'changed' : 'default'}
                  title={changed ? `Was ${k}=${target.baseTags[k] ?? '(key only)'}` : undefined}
                  onRemove={!exempt && !removed ? () => unsetTag([target.key], k) : undefined}
                />
                {(removed || added || changed) && (
                  <button
                    type="button"
                    onClick={() => undoField(target.key, k)}
                    className="text-gray-400 hover:text-gray-700"
                    aria-label={`Undo the edit to ${k}`}
                    title="Undo"
                  >
                    <Undo2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      </div>
      <AddTagForm compact submitLabel="Add or change" onAdd={(k, v) => setTag([target.key], k, v)} />
      <div>
        <label htmlFor={commentId} className="mb-1 flex items-center gap-2 text-xs font-medium text-gray-600">
          Description
          {target.comment && (
            <button type="button" onClick={() => undoField(target.key, null)} className="font-normal text-primary hover:underline">
              Undo
            </button>
          )}
        </label>
        <Textarea
          id={commentId}
          autoResize
          rows={2}
          value={comment ?? ''}
          onChange={(e) => setComment([target.key], e.target.value)}
          placeholder="No description"
          className={target.comment ? 'max-h-40 border-amber-300 bg-amber-50/50 text-sm' : 'max-h-40 text-sm'}
        />
        {target.comment?.value === null && target.baseComment !== null && (
          <p className="mt-1 text-xs text-amber-800">The description will be cleared.</p>
        )}
      </div>
    </div>
  );
}
