import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { listChangeSets } from '../../../services/metadataApi';
import type { ChangeSource } from '../../../types/metadata';
import { Input } from '../../ui/input';
import { Badge, EmptyState, ErrorBanner, Pager, Skeleton } from '../../rbac/ui';
import { ActorTypeBadge, SourceBadge } from './badges';
import { useMetadata } from './context';
import { SOURCE_LABELS, changeSetCounts, errorMessage, formatTime, relativeTime } from './format';
import { useAsync, useDebounced } from './useAsync';

const PAGE_SIZE = 25;

/** Every saved change, newest first: who, how, why and what it touched. */
export function ChangesTab() {
  const { refreshToken, openChangeSet } = useMetadata();
  const [actor, setActor] = useState('');
  const [name, setName] = useState('');
  const [source, setSource] = useState<ChangeSource | ''>('');
  const [page, setPage] = useState(1);
  const debouncedActor = useDebounced(actor.trim(), 300);
  const debouncedName = useDebounced(name.trim(), 300);

  const load = useMemo(
    () => () => listChangeSets({ actor: debouncedActor || undefined, name: debouncedName || undefined, source: source || undefined, page, page_size: PAGE_SIZE }),
    [debouncedActor, debouncedName, source, page],
  );
  const { data, error, loading, reload } = useAsync(load, { keepPrevious: true, token: refreshToken });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs font-medium text-gray-600">
          Who
          <Input
            value={actor}
            onChange={(e) => {
              setActor(e.target.value);
              setPage(1);
            }}
            placeholder="Name, email or application ID"
            className="mt-1 h-9 w-64"
          />
        </label>
        <label className="text-xs font-medium text-gray-600">
          Object
          <Input
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setPage(1);
            }}
            placeholder="catalog.schema.table"
            className="mt-1 h-9 w-64 font-mono"
          />
        </label>
        <label className="text-xs font-medium text-gray-600">
          Made with
          <select
            value={source}
            onChange={(e) => {
              setSource(e.target.value as ChangeSource | '');
              setPage(1);
            }}
            className="mt-1 block h-9 rounded-md border border-gray-300 bg-white px-2 text-sm"
          >
            <option value="">Anything</option>
            {(Object.keys(SOURCE_LABELS) as ChangeSource[]).map((s) => (
              <option key={s} value={s}>
                {SOURCE_LABELS[s]}
              </option>
            ))}
          </select>
        </label>
        {data && <span className="pb-2 text-sm text-gray-500">{data.total.toLocaleString()} changes</span>}
      </div>

      {error ? <ErrorBanner message={errorMessage(error, 'Couldn’t load changes')} onRetry={reload} /> : null}
      {!data && loading ? (
        <Skeleton rows={6} />
      ) : data && data.items.length === 0 ? (
        <EmptyState title="No changes found">{actor || name || source ? 'Try different filters.' : 'Saved changes appear here.'}</EmptyState>
      ) : data ? (
        <div className={loading ? 'opacity-60' : undefined}>
          <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200 bg-white">
            {data.items.map((cs) => (
              <li key={cs.id}>
                <button
                  type="button"
                  onClick={() => openChangeSet(cs.id)}
                  className="block w-full px-4 py-3 text-left hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
                >
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-medium text-gray-900">{cs.actor}</span>
                    <ActorTypeBadge type={cs.actor_type} />
                    <SourceBadge source={cs.source} />
                    {cs.reverted_by && <Badge tone="amber">Reverted</Badge>}
                    {cs.reverts_change_set_id && <Badge tone="gray">Undoes an earlier change</Badge>}
                    {cs.request_id && <Badge tone="indigo">Approved request</Badge>}
                    <span className="ml-auto text-xs text-gray-500" title={formatTime(cs.created_at)}>
                      {relativeTime(cs.created_at)}
                    </span>
                  </div>
                  {cs.reason && <p className="mt-1 line-clamp-2 text-sm text-gray-700">{cs.reason}</p>}
                  <p className="mt-1 text-xs text-gray-500">{changeSetCounts(cs).join(' · ')}</p>
                </button>
                {cs.request_id && (
                  <div className="px-4 pb-2 text-xs">
                    <Link to={`/requests/${cs.request_id}`} className="text-primary hover:underline">
                      Open the approval request
                    </Link>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {data && <Pager offset={(page - 1) * PAGE_SIZE} limit={PAGE_SIZE} total={data.total} onChange={(o) => setPage(Math.floor(o / PAGE_SIZE) + 1)} />}
    </div>
  );
}
