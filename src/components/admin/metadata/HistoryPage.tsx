import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { SlidersHorizontal } from 'lucide-react';
import { listChangeSets } from '../../../services/metadataApi';
import type { ChangeSource } from '../../../types/metadata';
import { Input } from '../../ui/input';
import { Badge, EmptyState, ErrorBanner, Pager, Skeleton } from '../../rbac/ui';
import { cn } from '../../../lib/utils';
import { ActorTypeBadge, SourceBadge } from './badges';
import { useMetadata } from './context';
import { SOURCE_LABELS, changeSetCounts, errorMessage, formatTime, relativeTime } from './format';
import { Popover } from './Popover';
import { useAsync, useDebounced } from './useAsync';

const PAGE_SIZE = 25;

type Scope = 'mine' | 'everyone';

/** Saved changes, newest first: your own by default, or everyone's. */
export function HistoryPage() {
  const { refreshToken, openChangeSet, whoami } = useMetadata();
  const [scope, setScope] = useState<Scope>('mine');
  const [actor, setActor] = useState('');
  const [name, setName] = useState('');
  const [source, setSource] = useState<ChangeSource | ''>('');
  const [page, setPage] = useState(1);
  const debouncedActor = useDebounced(actor.trim(), 300);
  const debouncedName = useDebounced(name.trim(), 300);
  const who = scope === 'mine' ? whoami.actor.id : debouncedActor || undefined;
  const filterCount = Number(!!name.trim()) + Number(!!source) + Number(scope === 'everyone' && !!actor.trim());

  const load = useMemo(
    () => () => listChangeSets({ actor: who, name: debouncedName || undefined, source: source || undefined, page, page_size: PAGE_SIZE }),
    [who, debouncedName, source, page],
  );
  const { data, error, loading, reload } = useAsync(load, { keepPrevious: true, token: refreshToken });
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div role="radiogroup" aria-label="Whose changes" className="inline-flex rounded-md border border-gray-300 bg-white p-0.5">
          {(
            [
              ['mine', 'My changes'],
              ['everyone', 'Everyone’s'],
            ] as [Scope, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={scope === value}
              onClick={() => reset(setScope)(value)}
              className={cn('rounded px-3 py-1.5 text-sm', scope === value ? 'bg-primary text-white' : 'text-gray-700 hover:bg-gray-100')}
            >
              {label}
            </button>
          ))}
        </div>
        <Popover
          label={
            <>
              <SlidersHorizontal className="h-4 w-4" /> Filters
              {filterCount > 0 && <span className="rounded-full bg-primary px-1.5 text-xs text-white">{filterCount}</span>}
            </>
          }
          ariaLabel="Filters"
          className="w-72 space-y-3 p-4"
        >
          {scope === 'everyone' && (
            <label className="block text-xs font-medium text-gray-600">
              Who
              <Input value={actor} onChange={(e) => reset(setActor)(e.target.value)} placeholder="Name, email or application ID" className="mt-1 h-9" />
            </label>
          )}
          <label className="block text-xs font-medium text-gray-600">
            Table or column
            <Input value={name} onChange={(e) => reset(setName)(e.target.value)} placeholder="catalog.schema.table" className="mt-1 h-9 font-mono" />
          </label>
          <label className="block text-xs font-medium text-gray-600">
            Made with
            <select
              value={source}
              onChange={(e) => reset(setSource)(e.target.value as ChangeSource | '')}
              className="mt-1 block h-9 w-full rounded-md border border-gray-300 bg-white px-2 text-sm"
            >
              <option value="">Anything</option>
              {(Object.keys(SOURCE_LABELS) as ChangeSource[]).map((s) => (
                <option key={s} value={s}>
                  {SOURCE_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
        </Popover>
        {data && <span className="text-sm text-gray-500">{data.total.toLocaleString()} changes</span>}
        <Link to="/requests" className="ml-auto text-sm text-primary hover:underline">
          Changes waiting for approval are under My requests
        </Link>
      </div>

      {error ? <ErrorBanner message={errorMessage(error, 'Couldn’t load changes')} onRetry={reload} /> : null}
      {!data && loading ? (
        <Skeleton rows={6} />
      ) : data && data.items.length === 0 ? (
        <EmptyState title={scope === 'mine' && !filterCount ? 'You haven’t changed anything yet' : 'No changes found'}>
          {filterCount ? 'Try different filters.' : scope === 'mine' ? 'Changes you save appear here. Switch to Everyone’s to see what others changed.' : 'Saved changes appear here.'}
        </EmptyState>
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
                    {scope === 'everyone' && (
                      <>
                        <span className="font-medium text-gray-900">{cs.actor}</span>
                        <ActorTypeBadge type={cs.actor_type} />
                      </>
                    )}
                    <SourceBadge source={cs.source} />
                    {cs.reverted_by && <Badge tone="amber">Undone</Badge>}
                    {cs.reverts_change_set_id && <Badge tone="gray">Undoes an earlier change</Badge>}
                    {cs.request_id && <Badge tone="indigo">Approved</Badge>}
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
