import { useMemo, useState } from 'react';
import { getDrift } from '../../../services/metadataApi';
import type { DriftEntry, ObjectSummary } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { EmptyState, ErrorBanner, Pager, Skeleton } from '../../rbac/ui';
import { EnvBadge } from './badges';
import { useMetadata } from './context';
import { DRIFT_KIND_LABELS, errorMessage, formatTime, objectLabel, plural, relativeTime, summaryDrift } from './format';
import { seedFromSummary, useStagingStore } from './staging';
import { TagChip } from './TagChips';
import { useAsync } from './useAsync';

const PAGE_SIZE = 25;

/** Objects whose Unity Catalog tags or description differ from what's saved here, key by key. */
export function DriftTab() {
  const { config, refreshToken } = useMetadata();
  const [catalog, setCatalog] = useState('');
  const [page, setPage] = useState(1);
  const load = useMemo(() => () => getDrift({ catalog: catalog || undefined, page, page_size: PAGE_SIZE }), [catalog, page]);
  const { data, error, loading, reload } = useAsync(load, { keepPrevious: true, token: refreshToken });
  const catalogs = data?.facets?.catalog ?? [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-gray-700">
          Catalog
          <select
            value={catalog}
            onChange={(e) => {
              setCatalog(e.target.value);
              setPage(1);
            }}
            className="h-9 rounded-md border border-gray-300 bg-white px-2 text-sm"
          >
            <option value="">All catalogs</option>
            {catalogs.map((c) => (
              <option key={c.value} value={c.value}>
                {c.value} ({c.count})
              </option>
            ))}
            {catalog && !catalogs.some((c) => c.value === catalog) && <option value={catalog}>{catalog}</option>}
          </select>
        </label>
        {data && <span className="text-sm text-gray-500">{plural(data.total, 'object')} out of sync</span>}
      </div>

      {error ? <ErrorBanner message={errorMessage(error, 'Couldn’t load this list')} onRetry={reload} /> : null}
      {!data && loading ? (
        <Skeleton rows={6} />
      ) : data && data.items.length === 0 ? (
        <EmptyState title="Everything matches">Unity Catalog matches everything saved here{catalog ? ` in ${catalog}` : ''}.</EmptyState>
      ) : data ? (
        <ul className={loading ? 'list-plain space-y-2 opacity-60' : 'list-plain space-y-2'}>
          {data.items.map((s) => (
            <DriftRow key={s.fqn_key} summary={s} entries={summaryDrift(s, config.exempt_keys)} />
          ))}
        </ul>
      ) : null}
      {data && <Pager offset={(page - 1) * PAGE_SIZE} limit={PAGE_SIZE} total={data.total} onChange={(o) => setPage(Math.floor(o / PAGE_SIZE) + 1)} />}
    </div>
  );
}

function DriftRow({ summary: s, entries }: { summary: ObjectSummary; entries: DriftEntry[] }) {
  const { openObject, config, notify } = useMetadata();
  const setTag = useStagingStore((st) => st.setTag);
  const unsetTag = useStagingStore((st) => st.unsetTag);
  const setComment = useStagingStore((st) => st.setComment);
  const fixable = entries.filter((e) => !e.exempt);

  // Accepting Unity Catalog's values means saving them here, so syncing stops undoing them.
  const acceptObserved = () => {
    const seed = seedFromSummary(s);
    fixable.forEach((e) => {
      if (e.field === 'comment') setComment([seed.key], e.observed ?? null, [seed]);
      else if (e.key && e.kind === 'missing') unsetTag([seed.key], e.key, [seed]);
      else if (e.key) setTag([seed.key], e.key, e.observed ?? null, [seed]);
    });
    notify('Added Unity Catalog’s values to your unsaved changes. Use Review and save at the bottom of the page when you’re ready.');
  };

  return (
    <li className="rounded-lg border border-gray-200 bg-white p-3">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => openObject(s.name, s.column)} className="font-mono text-sm font-medium text-gray-900 hover:text-primary hover:underline">
          {objectLabel(s.name, s.column)}
        </button>
        <EnvBadge env={s.environment} />
        {s.drift_detected_at && (
          <span className="text-xs text-gray-500" title={formatTime(s.drift_detected_at)}>
            noticed {relativeTime(s.drift_detected_at)}
          </span>
        )}
        {fixable.length > 0 && (
          <Button size="sm" variant="outline" className="ml-auto h-7 text-xs" onClick={acceptObserved} title="Save what Unity Catalog has here, so it’s kept">
            Keep Unity Catalog’s values
          </Button>
        )}
      </div>
      <table className="mt-2 w-full text-left text-xs">
        <thead className="text-gray-500">
          <tr>
            <th scope="col" className="py-1 pr-3 font-medium">Tag</th>
            <th scope="col" className="py-1 pr-3 font-medium">Saved here</th>
            <th scope="col" className="py-1 pr-3 font-medium">In Unity Catalog</th>
            <th scope="col" className="py-1 font-medium">What syncing does</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={`${e.field}-${e.key ?? ''}`} className={e.exempt ? 'border-t border-gray-100 text-gray-400' : 'border-t border-gray-100'}>
              <td className="py-1 pr-3 font-mono">{e.field === 'comment' ? 'description' : e.key}</td>
              <td className="py-1 pr-3">
                {e.kind === 'extra' ? <span className="text-gray-400">not set here</span> : e.field === 'tag' ? <TagChip tagKey={e.key ?? ''} value={e.desired} /> : <span>{e.desired}</span>}
              </td>
              <td className="py-1 pr-3">
                {e.kind === 'missing' ? <span className="text-gray-400">not set</span> : e.field === 'tag' ? <TagChip tagKey={e.key ?? ''} value={e.observed} tone="changed" /> : <span>{e.observed}</span>}
              </td>
              <td className="py-1">
                {e.exempt ? 'Leaves it: another tool manages it' : config.reconcile_mode === 'enforce' ? enforceText(e) : `${DRIFT_KIND_LABELS[e.kind]} (reported only)`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </li>
  );
}

function enforceText(e: DriftEntry): string {
  if (e.kind === 'extra') return e.field === 'comment' ? 'Clears the description' : 'Removes the tag';
  if (e.kind === 'missing') return e.field === 'comment' ? 'Writes the description' : 'Adds the tag';
  return 'Puts the value saved here back';
}
