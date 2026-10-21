import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { searchObjects } from '../../../services/metadataApi';
import { RELATION_TYPES, type ObjectSummary } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { EmptyState, ErrorBanner, Pager, Skeleton } from '../../rbac/ui';
import { useMetadata } from './context';
import { EditManyDialog } from './EditManyDialog';
import { errorMessage, plural } from './format';
import { Breadcrumb } from './ObjectHeader';
import { ObjectList } from './ObjectList';
import { datasetsPath } from './paths';
import { effectiveTags, seedFromSummary, useStagingStore, type TargetSeed } from './staging';
import { TagChip } from './TagChips';
import { AddTagForm } from './TagEditor';
import { useAsync } from './useAsync';

const PAGE_SIZE = 500;
const DATASET_KEYS = ['dataset', 'data_set'];
const SHOWN_VALUES = 3;

/** One dataset's tables and views, with its tags summarised and editable on all of them at once. */
export function DatasetPage() {
  const { dataset = '' } = useParams<{ dataset: string }>();
  const { refreshToken } = useMetadata();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Record<string, ObjectSummary>>({});
  const [editing, setEditing] = useState(false);
  const load = useMemo(
    () => () => searchObjects({ dataset, securable_types: RELATION_TYPES, include_unmanaged: true, sort: 'name', page, page_size: PAGE_SIZE }),
    [dataset, page],
  );
  const { data, error, loading, reload } = useAsync(load, { keepPrevious: true, token: refreshToken });
  const items = useMemo(() => data?.items ?? [], [data]);
  const catalogs = (data?.facets.catalog ?? []).map((f) => f.value).sort();
  const usedKeys = DATASET_KEYS.filter((k) => items.some((s) => seedFromSummary(s).baseTags[k] === dataset));
  const selectedList = Object.values(selected);

  const toggle = (s: ObjectSummary) =>
    setSelected((cur) => {
      const next = { ...cur };
      if (next[s.fqn_key]) delete next[s.fqn_key];
      else next[s.fqn_key] = s;
      return next;
    });
  const toggleAll = (on: boolean) =>
    setSelected((cur) => {
      const next = { ...cur };
      items.forEach((s) => (on ? (next[s.fqn_key] = s) : delete next[s.fqn_key]));
      return next;
    });

  return (
    <div className="space-y-4">
      <Breadcrumb dataset={dataset} />
      <header className="space-y-1 rounded-lg border border-gray-200 bg-white p-4">
        <div className="flex flex-wrap items-start gap-2">
          <h3 className="break-all font-mono text-lg font-semibold text-gray-900">{dataset}</h3>
          <span className="mt-1 text-xs text-gray-500">Dataset</span>
        </div>
        {data && (
          <p className="text-sm text-gray-600">
            {plural(data.total, 'table or view', 'tables and views')} tagged{' '}
            {(usedKeys.length ? usedKeys : DATASET_KEYS.slice(0, 1)).map((k, i) => (
              <span key={k}>
                {i > 0 && ' or '}
                <code>
                  {k}={dataset}
                </code>
              </span>
            ))}
            {catalogs.length > 0 && (
              <>
                {' '}
                in <span className="font-mono">{catalogs.join(', ')}</span>
              </>
            )}
            .
          </p>
        )}
      </header>

      {error ? <ErrorBanner message={errorMessage(error, 'Couldn’t load this dataset')} onRetry={reload} /> : null}
      {!data && loading ? (
        <Skeleton rows={8} />
      ) : data && data.total === 0 ? (
        <EmptyState title="Nothing is in this dataset">
          No table or view is tagged <code>dataset={dataset}</code> right now.{' '}
          <Link to={datasetsPath} className="underline">
            See all datasets
          </Link>
        </EmptyState>
      ) : data ? (
        <>
          <DatasetTags items={items} total={data.total} />
          {selectedList.length > 0 && (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-primary/30 bg-primary/5 px-4 py-2 text-sm">
              <span className="font-medium text-gray-900">{selectedList.length} selected</span>
              <Button size="sm" onClick={() => setEditing(true)}>
                {selectedList.length === 1 ? 'Edit tags and description' : `Edit these ${selectedList.length}`}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSelected({})}>
                Clear selection
              </Button>
            </div>
          )}
          <div className={loading ? 'space-y-2 opacity-60' : 'space-y-2'}>
            <ObjectList items={items} showPath selected={selected} onToggle={toggle} onToggleAll={toggleAll} />
            <Pager offset={(page - 1) * data.page_size} limit={data.page_size} total={data.total} onChange={(o) => setPage(Math.floor(o / data.page_size) + 1)} />
          </div>
        </>
      ) : null}

      {editing && (
        <EditManyDialog
          seeds={selectedList.map(seedFromSummary)}
          onClose={() => {
            setEditing(false);
            setSelected({});
          }}
        />
      )}
    </div>
  );
}

/** Every tag on the dataset's tables, how many carry it, and ways to put it on all or none of them. */
function DatasetTags({ items, total }: { items: ObjectSummary[]; total: number }) {
  const { config } = useMetadata();
  const targets = useStagingStore((s) => s.targets);
  const setTag = useStagingStore((s) => s.setTag);
  const unsetTag = useStagingStore((s) => s.unsetTag);
  const seeds = useMemo(() => items.map(seedFromSummary), [items]);
  const tagsOf = (seed: TargetSeed) => (targets[seed.key] ? effectiveTags(targets[seed.key]) : seed.baseTags);

  // tag key -> value ('' for a key-only tag) -> how many tables have it
  const byKey = new Map<string, Map<string, number>>();
  seeds.forEach((seed) =>
    Object.entries(tagsOf(seed)).forEach(([key, value]) => {
      if (DATASET_KEYS.includes(key)) return;
      const values = byKey.get(key) ?? new Map<string, number>();
      values.set(value ?? '', (values.get(value ?? '') ?? 0) + 1);
      byKey.set(key, values);
    }),
  );
  const rows = [...byKey]
    .map(([key, values]) => ({
      key,
      values: [...values].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
      count: [...values.values()].reduce((a, b) => a + b, 0),
    }))
    .sort((a, b) => Number(config.exempt_keys.includes(a.key)) - Number(config.exempt_keys.includes(b.key)) || a.key.localeCompare(b.key));

  const setOn = (on: TargetSeed[], key: string, value: string | null) => setTag(on.map((s) => s.key), key, value, on);
  const removeFromAll = (key: string) => {
    const on = seeds.filter((s) => key in tagsOf(s));
    unsetTag(on.map((s) => s.key), key, on);
  };
  const n = seeds.length;

  return (
    <section aria-label="Tags across this dataset" className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
      <div>
        <h4 className="text-sm font-semibold text-gray-900">Tags across this dataset</h4>
        <p className="text-xs text-gray-500">
          {total > n ? `Covers the ${n.toLocaleString()} tables on this page. ` : ''}
          Changes here join your unsaved changes. Nothing is saved until you choose Review and save at the bottom.
        </p>
      </div>
      {rows.length === 0 ? (
        <p className="text-xs text-gray-400">No other tags on these tables yet.</p>
      ) : (
        <ul className="list-plain divide-y divide-gray-100">
          {rows.map(({ key, values, count }) => {
            const exempt = config.exempt_keys.includes(key);
            const single = values.length === 1 ? values[0][0] : undefined;
            const missing = n - count;
            return (
              <li key={key} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                  <TagChip tagKey={key} value={single === undefined ? null : single || null} tone={exempt ? 'exempt' : 'default'} />
                  {single === undefined && (
                    <span className="text-xs text-gray-500" title={values.map(([v, c]) => `${v || '(no value)'}: ${c}`).join('\n')}>
                      {values.length} different values: {values.slice(0, SHOWN_VALUES).map(([v]) => v || '(no value)').join(', ')}
                      {values.length > SHOWN_VALUES ? ', …' : ''}
                    </span>
                  )}
                </div>
                <span className="text-xs tabular-nums text-gray-500">{missing === 0 ? `On all ${n}` : `On ${count} of ${n}`}</span>
                {exempt ? (
                  <span className="text-xs text-gray-400">Managed outside this tool</span>
                ) : (
                  <div className="flex items-center gap-1">
                    {single !== undefined && missing > 0 && (
                      <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => setOn(seeds.filter((s) => !(key in tagsOf(s))), key, single || null)}>
                        Add to the other {missing}
                      </Button>
                    )}
                    <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => removeFromAll(key)}>
                      Remove from all
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <div className="space-y-1 border-t border-gray-100 pt-3">
        <div className="text-sm font-medium text-gray-900">Add or change a tag on every table</div>
        <AddTagForm compact submitLabel="Add to all" onAdd={(key, value) => setOn(seeds, key, value)} />
      </div>
    </section>
  );
}
