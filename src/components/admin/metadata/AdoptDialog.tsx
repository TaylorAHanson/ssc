import { useCallback, useId, useMemo, useState } from 'react';
import { adoptState, searchObjects } from '../../../services/metadataApi';
import type { ChangeResult } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { Switch } from '../../ui/switch';
import { Textarea } from '../../ui/textarea';
import { ErrorBanner, Modal, Notice, Spinner } from '../../rbac/ui';
import { useMetadata } from './context';
import { errorMessage, plural, reasonHint } from './format';
import { ResultView } from './ResultView';
import { useAsync } from './useAsync';

/** Start managing what's already in Unity Catalog by saving its current tags and descriptions here. */
export function AdoptDialog({ onClose }: { onClose: () => void }) {
  const { config, bumpRefresh } = useMetadata();
  const [catalogs, setCatalogs] = useState<string[]>([]);
  const [names, setNames] = useState('');
  const [overwrite, setOverwrite] = useState(false);
  const [includeColumns, setIncludeColumns] = useState(true);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ChangeResult | null>(null);
  const id = useId();

  const loadCatalogs = useCallback(() => searchObjects({ include_unmanaged: true, page_size: 1 }), []);
  const { data: all } = useAsync(loadCatalogs);
  const catalogFacets = all?.facets.catalog ?? [];

  const nameList = names.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
  // There's no adopt preview, so estimate from search: everything in scope vs what's managed already.
  const estimateKey = JSON.stringify([catalogs, includeColumns]);
  const loadEstimate = useMemo(() => {
    const [cats, cols] = JSON.parse(estimateKey) as [string[], boolean];
    if (!cats.length) return null;
    return async () => {
      const [everything, managed] = await Promise.all([
        searchObjects({ catalogs: cats, include_unmanaged: true, include_columns: cols, page_size: 1 }),
        searchObjects({ catalogs: cats, include_columns: cols, page_size: 1 }),
      ]);
      return { total: everything.total, managed: managed.total };
    };
  }, [estimateKey]);
  const estimate = useAsync(loadEstimate);

  const reasonOk = reason.trim().length >= config.min_reason_length;
  const canSubmit = (catalogs.length > 0 || nameList.length > 0) && reasonOk && !busy;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await adoptState({ catalogs, names: nameList, overwrite, include_columns: includeColumns, reason: reason.trim() });
      setResult(res);
      bumpRefresh();
    } catch (e) {
      setError(errorMessage(e, 'Couldn’t start managing these'));
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    return (
      <Modal title="Start managing catalogs" onClose={onClose} footer={<Button onClick={onClose}>Done</Button>}>
        <ResultView result={result} onLeave={onClose} />
      </Modal>
    );
  }

  return (
    <Modal
      title="Start managing catalogs"
      subtitle="Saves what Unity Catalog has now as the starting point here, so syncing keeps it that way. Nothing in Unity Catalog changes."
      onClose={onClose}
      wide
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!canSubmit}>
            {busy ? 'Saving…' : 'Start managing'}
          </Button>
        </>
      }
    >
      <fieldset>
        <legend className="mb-1 text-sm font-medium text-gray-900">Catalogs</legend>
        {!all ? (
          <Spinner label="Loading catalogs…" />
        ) : (
          <div className="grid max-h-48 gap-1 overflow-y-auto sm:grid-cols-2">
            {catalogFacets.map((c) => (
              <label key={c.value} className="inline-flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={catalogs.includes(c.value)}
                  onChange={(e) => setCatalogs(e.target.checked ? [...catalogs, c.value] : catalogs.filter((x) => x !== c.value))}
                  className="h-3.5 w-3.5 rounded border-gray-300"
                />
                <span className="font-mono">{c.value}</span>
                <span className="text-xs text-gray-400">{c.count.toLocaleString()}</span>
              </label>
            ))}
          </div>
        )}
      </fieldset>
      <div>
        <label htmlFor={`${id}-names`} className="mb-1 block text-sm font-medium text-gray-900">
          Or specific objects <span className="font-normal text-gray-500">(optional, one per line; a schema includes its tables)</span>
        </label>
        <Textarea id={`${id}-names`} rows={2} value={names} onChange={(e) => setNames(e.target.value)} placeholder="catalog.schema.table" className="font-mono text-sm" />
      </div>
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Switch id={`${id}-cols`} checked={includeColumns} onCheckedChange={setIncludeColumns} />
          <label htmlFor={`${id}-cols`} className="text-sm text-gray-800">
            Include columns
          </label>
        </div>
        <div className="flex items-start gap-2">
          <Switch id={`${id}-over`} checked={overwrite} onCheckedChange={setOverwrite} className="mt-0.5" />
          <label htmlFor={`${id}-over`} className="text-sm text-gray-800">
            Also reset objects that are already managed
            <span className="block text-xs text-gray-500">The tags and descriptions saved here are replaced with what Unity Catalog has, so nothing is out of sync.</span>
          </label>
        </div>
      </div>
      {catalogs.length > 0 && (
        <Notice kind={overwrite ? 'warn' : 'info'}>
          {estimate.loading || !estimate.data ? (
            'Counting objects…'
          ) : (
            <>
              About {plural(estimate.data.total - estimate.data.managed, 'object')} in these catalogs aren’t managed yet and will be.
              {overwrite && estimate.data.managed > 0 && ` ${plural(estimate.data.managed, 'managed object')} will be reset to Unity Catalog’s values.`}
              {!overwrite && estimate.data.managed > 0 && ` ${plural(estimate.data.managed, 'object')} already managed stay as they are.`}
            </>
          )}
        </Notice>
      )}
      <div>
        <label htmlFor={`${id}-reason`} className="mb-1 block text-sm font-medium text-gray-900">
          Reason <span className="font-normal text-gray-500">({reasonHint(config.min_reason_length)})</span>
        </label>
        <Textarea id={`${id}-reason`} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Bringing the finance catalogs under management" className="text-sm" />
      </div>
      {error && <ErrorBanner message={error} />}
    </Modal>
  );
}
