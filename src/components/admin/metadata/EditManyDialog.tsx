import { useId, useState } from 'react';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Modal } from '../../rbac/ui';
import { useMetadata } from './context';
import { plural } from './format';
import { effectiveTags, useStagingStore, type TargetSeed } from './staging';
import { AddTagForm } from './TagEditor';

/** Add, remove or describe the same way on every selected object; the edits join the unsaved changes. */
export function EditManyDialog({ seeds, onClose }: { seeds: TargetSeed[]; onClose: () => void }) {
  const { config, notify } = useMetadata();
  const setTag = useStagingStore((s) => s.setTag);
  const unsetTag = useStagingStore((s) => s.unsetTag);
  const setComment = useStagingStore((s) => s.setComment);
  const targets = useStagingStore((s) => s.targets);
  const [removeKey, setRemoveKey] = useState('');
  const [comment, setCommentText] = useState('');
  const [done, setDone] = useState<string[]>([]);
  const ids = useId();
  const keys = seeds.map((s) => s.key);
  const existingKeys = [
    ...new Set(seeds.flatMap((s) => Object.keys(targets[s.key] ? effectiveTags(targets[s.key]) : s.baseTags))),
  ]
    .filter((k) => !config.exempt_keys.includes(k))
    .sort();
  const record = (text: string) => setDone((d) => [...d, text]);

  const finish = () => {
    if (done.length) notify('Added to your unsaved changes. Use Review and save at the bottom of the page when you’re ready.');
    onClose();
  };

  return (
    <Modal
      title={`Edit ${plural(seeds.length, 'object')}`}
      subtitle="Whatever you do here applies to every selected object. Nothing is saved until you review it."
      onClose={finish}
      footer={<Button onClick={finish}>Done</Button>}
    >
      <div className="space-y-5">
        <div className="space-y-1">
          <div className="text-sm font-medium text-gray-900">Add or change a tag</div>
          <AddTagForm
            compact
            submitLabel="Add to all"
            onAdd={(k, v) => {
              setTag(keys, k, v, seeds);
              record(v === null ? `Add ${k}` : `Set ${k} = ${v}`);
            }}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor={`${ids}-remove`} className="text-sm font-medium text-gray-900">
            Remove a tag
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <select id={`${ids}-remove`} value={removeKey} onChange={(e) => setRemoveKey(e.target.value)} className="h-8 rounded-md border border-gray-300 bg-white px-2 text-xs">
              <option value="">Choose a tag</option>
              {existingKeys.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
            <Button
              size="sm"
              variant="outline"
              className="h-8 text-xs"
              disabled={!removeKey}
              onClick={() => {
                unsetTag(keys, removeKey, seeds);
                record(`Remove ${removeKey}`);
                setRemoveKey('');
              }}
            >
              Remove from all
            </Button>
          </div>
        </div>
        <div className="space-y-1">
          <label htmlFor={`${ids}-comment`} className="text-sm font-medium text-gray-900">
            Description
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <Input id={`${ids}-comment`} value={comment} onChange={(e) => setCommentText(e.target.value)} placeholder="New description" className="h-8 w-72 text-xs" />
            <Button
              size="sm"
              variant="outline"
              className="h-8 text-xs"
              disabled={!comment.trim()}
              onClick={() => {
                setComment(keys, comment, seeds);
                record('Set the description');
                setCommentText('');
              }}
            >
              Set on all
            </Button>
          </div>
        </div>
        {done.length > 0 && (
          <div className="rounded-md bg-gray-50 p-3 text-xs text-gray-700">
            <div className="mb-1 font-medium">Added to your unsaved changes</div>
            <ul className="list-inside list-disc">
              {done.map((d, i) => (
                <li key={i}>{d}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Modal>
  );
}
