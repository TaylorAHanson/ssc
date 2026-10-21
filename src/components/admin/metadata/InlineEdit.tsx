import { useState } from 'react';
import { Pencil, Plus, Undo2 } from 'lucide-react';
import { Button } from '../../ui/button';
import { Textarea } from '../../ui/textarea';
import { cn } from '../../../lib/utils';
import { useMetadata } from './context';
import { Popover } from './Popover';
import { effectiveComment, useStagingStore, type StagedTarget, type TargetSeed } from './staging';
import { AddTagForm } from './TagEditor';
import { TagChip } from './TagChips';

/** The object as it would be after this person's unsaved changes. */
function useTarget(seed: TargetSeed): StagedTarget {
  const staged = useStagingStore((s) => s.targets[seed.key]);
  return staged ?? { ...seed, set: {}, unset: [] };
}

/** Tag chips that can be removed in place, with an "Add tag" button; edits stay unsaved until reviewed. */
export function TagsField({ seed, readOnly, compact }: { seed: TargetSeed; readOnly?: boolean; compact?: boolean }) {
  const { config } = useMetadata();
  const target = useTarget(seed);
  const setTag = useStagingStore((s) => s.setTag);
  const unsetTag = useStagingStore((s) => s.unsetTag);
  const undoField = useStagingStore((s) => s.undoField);
  const keys = [...new Set([...Object.keys(target.baseTags), ...Object.keys(target.set)])].sort(
    (a, b) => Number(config.exempt_keys.includes(a)) - Number(config.exempt_keys.includes(b)) || a.localeCompare(b),
  );

  return (
    <ul className="list-plain flex flex-wrap items-center gap-1.5" aria-label="Tags">
      {keys.length === 0 && readOnly && <li className="text-xs text-gray-400">No tags</li>}
      {keys.map((k) => {
        const exempt = config.exempt_keys.includes(k);
        const removed = target.unset.includes(k);
        const added = k in target.set && !(k in target.baseTags);
        const changed = k in target.set && k in target.baseTags;
        return (
          <li key={k} className="inline-flex items-center gap-0.5">
            <TagChip
              tagKey={k}
              value={k in target.set ? target.set[k] : target.baseTags[k]}
              tone={exempt ? 'exempt' : removed ? 'removed' : added ? 'added' : changed ? 'changed' : 'default'}
              title={exempt ? `${k} is managed by another tool, so it can't be changed here.` : changed ? `Was ${k}=${target.baseTags[k] ?? '(no value)'}` : undefined}
              onRemove={!readOnly && !exempt && !removed ? () => unsetTag([seed.key], k, [seed]) : undefined}
            />
            {(removed || added || changed) && (
              <button type="button" onClick={() => undoField(seed.key, k)} className="text-gray-400 hover:text-gray-700" aria-label={`Undo the change to ${k}`} title="Undo">
                <Undo2 className="h-3.5 w-3.5" />
              </button>
            )}
          </li>
        );
      })}
      {!readOnly && (
        <li>
          <Popover
            label={
              <>
                <Plus className="h-3.5 w-3.5" /> {compact ? 'Tag' : 'Add tag'}
              </>
            }
            ariaLabel="Add a tag"
            buttonClassName={cn('border-dashed text-gray-600', compact ? 'h-6 px-2 text-xs' : 'h-7 px-2.5 text-xs')}
            className="w-max p-3"
          >
            {(close) => (
              <AddTagForm
                compact
                onAdd={(k, v) => {
                  setTag([seed.key], k, v, [seed]);
                  close();
                }}
              />
            )}
          </Popover>
        </li>
      )}
    </ul>
  );
}

/** The description as text, edited in place; the change stays unsaved until reviewed. */
export function DescriptionField({ seed, readOnly, compact }: { seed: TargetSeed; readOnly?: boolean; compact?: boolean }) {
  const target = useTarget(seed);
  const setComment = useStagingStore((s) => s.setComment);
  const undoField = useStagingStore((s) => s.undoField);
  const [draft, setDraft] = useState<string | null>(null);
  const value = effectiveComment(target);

  if (draft !== null) {
    const done = () => {
      setComment([seed.key], draft, [seed]);
      setDraft(null);
    };
    return (
      <div className="space-y-1.5">
        <Textarea
          autoFocus
          autoResize
          rows={compact ? 1 : 2}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setDraft(null);
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) done();
          }}
          placeholder="Describe what this holds, in plain words"
          aria-label="Description"
          className="max-h-48 text-sm"
        />
        <div className="flex gap-2">
          <Button size="sm" className="h-7 text-xs" onClick={done}>
            Done
          </Button>
          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setDraft(null)}>
            Cancel
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="group flex items-start gap-1.5">
      <p
        className={cn(
          'whitespace-pre-wrap text-sm',
          value ? 'text-gray-700' : 'text-gray-400',
          target.comment && 'rounded bg-amber-50 px-1 ring-1 ring-amber-200',
          compact && 'line-clamp-2',
        )}
      >
        {value || (readOnly ? 'No description' : 'No description yet')}
      </p>
      {!readOnly && (
        <button
          type="button"
          onClick={() => setDraft(value ?? '')}
          className="mt-0.5 shrink-0 text-gray-400 hover:text-gray-700"
          aria-label={value ? 'Edit the description' : 'Add a description'}
          title={value ? 'Edit' : 'Add a description'}
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
      )}
      {target.comment && (
        <button type="button" onClick={() => undoField(seed.key, null)} className="mt-0.5 shrink-0 text-gray-400 hover:text-gray-700" aria-label="Undo the description change" title="Undo">
          <Undo2 className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
