import { useId, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { getObject, MetadataApiError } from '../../../services/metadataApi';
import { RELATION_TYPES, type SecurableType } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { ErrorBanner, Modal } from '../../rbac/ui';
import { useMetadata } from './context';
import { TYPE_LABELS, envForCatalog, errorMessage, inferType, nameProblem, objectLabel } from './format';
import { DescriptionField, TagsField } from './InlineEdit';
import { objectPath } from './paths';
import { hasEdits, seedForNew, seedFromDetail, useStagingStore, type TargetSeed } from './staging';

/** Prepare tags for an object that doesn't exist yet; they're applied when it's created. */
export function DeclareObjectDialog({ onClose }: { onClose: () => void }) {
  const { config, notify } = useMetadata();
  const navigate = useNavigate();
  const [seed, setSeed] = useState<TargetSeed | null>(null);
  const edited = useStagingStore((s) => (seed && s.targets[seed.key] ? hasEdits(s.targets[seed.key]) : false));
  const [name, setName] = useState('');
  const [column, setColumn] = useState('');
  const [type, setType] = useState<SecurableType | ''>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  const inferred = inferType(name, column);
  const relation = !column.trim() && name.trim().split('.').filter(Boolean).length === 3;
  const problem = name.trim() ? nameProblem(name, column) : null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const p = nameProblem(name, column);
    if (p) {
      setError(p);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const detail = await getObject(name.trim(), column.trim() || null);
      if (detail.desired?.status === 'active' || detail.observed?.present) {
        const existing = seedFromDetail(detail);
        navigate(objectPath(existing.name, existing.column));
        notify('That already exists, so it’s opened here for you to edit.');
        onClose();
        return;
      }
    } catch (err) {
      if (!(err instanceof MetadataApiError && err.status === 404)) {
        setError(errorMessage(err));
        setBusy(false);
        return;
      }
    }
    const securable = relation && type ? type : inferred;
    setSeed(seedForNew(name, column.trim() || null, securable, envForCatalog(config, name.split('.')[0])));
    setBusy(false);
  };

  const title = 'Prepare tags for something that doesn’t exist yet';
  if (seed) {
    const finish = () => {
      if (edited) notify('Added to your unsaved changes. Use Review and save at the bottom of the page when you’re ready.');
      onClose();
    };
    return (
      <Modal title={title} subtitle={<span className="font-mono">{objectLabel(seed.name, seed.column)}</span>} onClose={finish} footer={<Button onClick={finish}>Done</Button>}>
        <div className="space-y-4">
          <div>
            <div className="mb-1 text-xs font-medium text-gray-500">Tags</div>
            <TagsField seed={seed} />
          </div>
          <div>
            <div className="mb-1 text-xs font-medium text-gray-500">Description</div>
            <DescriptionField seed={seed} />
          </div>
          <p className="text-xs text-gray-500">These are applied as soon as it’s created in Unity Catalog.</p>
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      title={title}
      subtitle="Set tags and a description now. They’re applied as soon as the table is created in Unity Catalog."
      onClose={onClose}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form={`${id}-form`} disabled={busy || !name.trim() || !!problem}>
            {busy ? 'Checking…' : 'Next'}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} onSubmit={submit} className="space-y-3">
        <div>
          <label htmlFor={`${id}-name`} className="mb-1 block text-sm font-medium text-gray-800">
            Name
          </label>
          <Input
            id={`${id}-name`}
            data-autofocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="catalog.schema.table"
            className="font-mono"
            aria-invalid={!!problem}
            aria-describedby={`${id}-name-help`}
          />
          <p id={`${id}-name-help`} className={problem ? 'mt-1 text-xs text-red-700' : 'mt-1 text-xs text-gray-500'}>
            {problem ?? 'A catalog, schema or table name. Names are matched without regard to case.'}
          </p>
        </div>
        <div>
          <label htmlFor={`${id}-column`} className="mb-1 block text-sm font-medium text-gray-800">
            Column <span className="font-normal text-gray-500">(optional)</span>
          </label>
          <Input id={`${id}-column`} value={column} onChange={(e) => setColumn(e.target.value)} placeholder="column_name" className="font-mono" />
        </div>
        {relation && (
          <div>
            <label htmlFor={`${id}-type`} className="mb-1 block text-sm font-medium text-gray-800">
              Type
            </label>
            <select
              id={`${id}-type`}
              value={type}
              onChange={(e) => setType(e.target.value as SecurableType | '')}
              className="h-10 w-full rounded-md border border-gray-300 bg-white px-3 text-sm"
            >
              <option value="">Table (default)</option>
              {RELATION_TYPES.filter((t) => t !== 'table').map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABELS[t]}
                </option>
              ))}
            </select>
          </div>
        )}
        {name.trim() && !problem && (
          <p className="text-xs text-gray-500">
            Will be set up as a {TYPE_LABELS[relation && type ? type : inferred].toLowerCase()}
            {envForCatalog(config, name.split('.')[0]) && ` in the ${envForCatalog(config, name.split('.')[0])} environment`}.
          </p>
        )}
        {error && <ErrorBanner message={error} />}
      </form>
    </Modal>
  );
}
