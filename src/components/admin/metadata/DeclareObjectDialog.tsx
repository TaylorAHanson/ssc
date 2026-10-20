import { useId, useState, type FormEvent } from 'react';
import { getObject, MetadataApiError } from '../../../services/metadataApi';
import { RELATION_TYPES, type SecurableType } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { ErrorBanner, Modal } from '../../rbac/ui';
import { useMetadata } from './context';
import { TYPE_LABELS, envForCatalog, errorMessage, inferType, nameProblem } from './format';
import { seedForNew, seedFromDetail, useStagingStore } from './staging';

/** Declare tags for an object that doesn't exist yet; they're applied when it's created. */
export function DeclareObjectDialog({ onClose }: { onClose: () => void }) {
  const { config, notify } = useMetadata();
  const stage = useStagingStore((s) => s.stage);
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
        stage([seedFromDetail(detail)]);
        notify('That object already exists, so it was staged for editing instead.');
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
    stage([seedForNew(name, column.trim() || null, securable, envForCatalog(config, name.split('.')[0]))]);
    onClose();
  };

  return (
    <Modal
      title="Declare a new object"
      subtitle="Set tags and a description before the object exists. They’re applied as soon as it’s created in Unity Catalog."
      onClose={onClose}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form={`${id}-form`} disabled={busy || !name.trim() || !!problem}>
            {busy ? 'Checking…' : 'Stage it'}
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
            Will be declared as a {TYPE_LABELS[relation && type ? type : inferred].toLowerCase()}
            {envForCatalog(config, name.split('.')[0]) && ` in the ${envForCatalog(config, name.split('.')[0])} environment`}.
          </p>
        )}
        {error && <ErrorBanner message={error} />}
      </form>
    </Modal>
  );
}
