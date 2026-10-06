import { useEffect, useMemo, useRef, useState } from 'react';
import { Database, FolderTree, Layers, Loader2, Search, Table2, X } from 'lucide-react';
import { api } from '../../../services/api';
import type { TagDataset, TagSearchResponse } from '../../../services/api';
import { errorText, isContainerName, typeLabel } from './tagModel';

const VISIBLE_OBJECTS = 8;
const VISIBLE_CONTAINERS = 4;
// While typing, datasets are capped so tables stay visible; "show more" lifts it.
const VISIBLE_DATASETS = 5;

type Item =
  | { kind: 'dataset'; id: string; scope: string }
  | {
      kind: 'object';
      group: 'container' | 'table';
      fqn: string;
      objectType: string;
      type: string;
      tagCount: number;
      inCache: boolean;
    };

const GROUP_LABELS = { container: 'Catalogs & schemas', table: 'Tables & views' };

function groupOf(item: Item): string {
  return item.kind === 'dataset' ? 'dataset' : item.group;
}

function matchesWords(name: string, query: string): boolean {
  const lowered = name.toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((w) => {
      if (!w.includes('*')) return lowered.includes(w);
      const pattern = w
        .split('*')
        .map((p) => p.replace(/[.+?^${}()|[\]\\]/g, '\\$&'))
        .join('.*');
      return new RegExp(`^${pattern}$`).test(lowered);
    });
}

// One box for everything. Click it to browse every governed dataset; type to
// find catalogs, schemas, tables and views too, by name, glob (main.sales.*), or tag
// (classification=restricted, data_owner=*, !data_owner). Each result's
// checkbox means "in my list": ticking adds it straight away, unticking removes it.
export function TagSearchBox({
  datasets,
  isDatasetAdded,
  datasetTableCount,
  onToggleDataset,
  isAdded,
  onAddObjects,
  onRemoveObject,
  listSize,
}: {
  datasets: TagDataset[];
  isDatasetAdded: (datasetId: string) => boolean;
  datasetTableCount: (datasetId: string) => number | undefined;
  onToggleDataset: (datasetId: string, add: boolean) => Promise<void> | void;
  isAdded: (fqn: string) => boolean;
  onAddObjects: (fqns: string[]) => Promise<void>;
  onRemoveObject: (fqn: string) => void;
  listSize: number;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TagSearchResponse | null>(null);
  const [active, setActive] = useState(-1);
  const [allDatasets, setAllDatasets] = useState(false);
  const [pending, setPending] = useState<Set<string>>(new Set());
  // Enter pressed before the latest results arrived; acted on when they do.
  const [pendingEnter, setPendingEnter] = useState(false);
  const latest = useRef(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const q = query.trim();
  const hasTagFilter = /[=!]/.test(q);

  useEffect(() => {
    setAllDatasets(false);
    if (!q) {
      setResult(null);
      setError(null);
      setLoading(false);
      return;
    }
    const id = ++latest.current;
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const res = await api.searchTagTargets(q);
        if (id === latest.current) {
          setResult(res);
          setError(null);
          setActive(-1);
        }
      } catch (e) {
        if (id === latest.current) setError(errorText(e, 'Search failed'));
      } finally {
        if (id === latest.current) setLoading(false);
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [q]);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  // Datasets are filtered here, over the full list, so every one is reachable.
  const matchingDatasets = useMemo(
    () => (hasTagFilter ? [] : datasets.filter((d) => !q || matchesWords(d.dataset_id, q))),
    [datasets, q, hasTagFilter]
  );
  const datasetCount = !q || allDatasets ? matchingDatasets.length : Math.min(VISIBLE_DATASETS, matchingDatasets.length);
  const hiddenDatasets = matchingDatasets.length - datasetCount;

  const items: Item[] = useMemo(() => {
    const ds: Item[] = matchingDatasets.slice(0, datasetCount).map((d) => ({
      kind: 'dataset',
      id: d.dataset_id,
      scope: [d.catalog, d.schema_name].filter(Boolean).join('.'),
    }));
    const found = q && result ? result.objects : [];
    const toItem = (o: TagSearchResponse['objects'][number], group: 'container' | 'table'): Item => ({
      kind: 'object',
      group,
      fqn: o.fqn,
      objectType: o.object_type ?? '',
      type: typeLabel(o.object_type),
      tagCount: o.tag_keys.length,
      inCache: o.in_cache,
    });
    const containers = found
      .filter((o) => isContainerName(o.fqn))
      .slice(0, VISIBLE_CONTAINERS)
      .map((o) => toItem(o, 'container'));
    const tables = found
      .filter((o) => !isContainerName(o.fqn))
      .slice(0, VISIBLE_OBJECTS)
      .map((o) => toItem(o, 'table'));
    return [...ds, ...containers, ...tables];
  }, [matchingDatasets, datasetCount, result, q]);

  const allFqns = q && result ? result.objects.map((o) => o.fqn) : [];
  const notYetAdded = allFqns.filter((f) => !isAdded(f) && !pending.has(f));
  const isPattern = /[*=!]/.test(q);

  const withPending = async (keys: string[], work: () => Promise<void> | void) => {
    setPending((prev) => new Set([...prev, ...keys]));
    try {
      await work();
    } finally {
      setPending((prev) => new Set([...prev].filter((k) => !keys.includes(k))));
    }
  };

  const pendingKey = (item: Item) => (item.kind === 'dataset' ? `ds:${item.id}` : item.fqn);

  const isChecked = (item: Item) =>
    pending.has(pendingKey(item)) || (item.kind === 'dataset' ? isDatasetAdded(item.id) : isAdded(item.fqn));

  const toggle = (item: Item) => {
    if (pending.has(pendingKey(item))) return;
    const on = !isChecked(item);
    if (item.kind === 'dataset') {
      withPending([pendingKey(item)], () => onToggleDataset(item.id, on));
    } else if (on) {
      withPending([item.fqn], () => onAddObjects([item.fqn]));
    } else {
      onRemoveObject(item.fqn);
    }
    inputRef.current?.focus();
  };

  const selectAll = () => {
    if (notYetAdded.length) withPending(notYetAdded, () => onAddObjects(notYetAdded));
  };

  const done = () => {
    setQuery('');
    setOpen(false);
  };

  // Enter: toggle the highlighted row, else select every match for a pattern,
  // else the only match.
  const submit = () => {
    if (active >= 0 && items[active]) toggle(items[active]);
    else if (isPattern) selectAll();
    else if (items.length === 1) toggle(items[0]);
  };

  useEffect(() => {
    if (pendingEnter && !loading) {
      setPendingEnter(false);
      submit();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingEnter, loading]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, items.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, -1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (loading) setPendingEnter(true);
      else submit();
    } else if (e.key === 'Escape') {
      if (open) setOpen(false);
      else setQuery('');
    }
  };

  const totalObjects = q && result ? result.total_objects : 0;
  const shownObjects = items.length - datasetCount;

  return (
    <div ref={boxRef} className="relative">
      <div className="relative">
        <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder={`Search ${datasets.length ? `${datasets.length} datasets, ` : ''}catalogs, schemas, tables and views…`}
          aria-label="Find datasets, catalogs, schemas, tables or views"
          className="w-full h-11 pl-9 pr-9 rounded-lg border border-gray-300 bg-white text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
        />
        {loading ? (
          <Loader2 className="w-4 h-4 animate-spin text-blue-600 absolute right-3 top-1/2 -translate-y-1/2" />
        ) : query ? (
          <button
            type="button"
            onClick={() => setQuery('')}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 p-0.5 rounded hover:bg-gray-100"
            title="Clear search"
          >
            <X className="w-4 h-4 text-gray-400" />
          </button>
        ) : null}
      </div>
      <p className="mt-1.5 text-[11px] text-gray-500">
        Click to browse datasets, or type a name — catalogs and schemas match too. Also try{' '}
        <code className="text-gray-700">main.sales.*</code>,{' '}
        <code className="text-gray-700">classification=restricted</code> or{' '}
        <code className="text-gray-700">!data_owner</code> (missing a tag).
      </p>

      {open && (
        <div className="absolute z-30 mt-1 w-full rounded-lg border border-gray-200 bg-white shadow-lg overflow-hidden">
          <div className="px-3 py-2 text-xs font-medium text-gray-700 bg-blue-50/60 border-b border-blue-100">
            Tick everything you want to edit — pick as many as you like.
          </div>
          {error && <div className="px-3 py-2.5 text-xs text-rose-700 bg-rose-50">{error}</div>}
          {!error && q && result && !loading && items.length === 0 && (
            <div className="px-3 py-3 text-xs text-gray-500">
              Nothing matches{result.filters.length ? ` (${result.filters.join(', ')})` : ''}.
            </div>
          )}
          {!q && datasets.length === 0 && (
            <div className="px-3 py-3 text-xs text-gray-500">
              No governed datasets yet — type to find catalogs, schemas, tables and views.
            </div>
          )}

          {items.length > 0 && (
            <ul role="listbox" aria-multiselectable="true" className="max-h-96 overflow-y-auto py-1">
              {items.map((item, idx) => {
                const header =
                  idx > 0 && groupOf(items[idx - 1]) === groupOf(item)
                    ? null
                    : item.kind === 'dataset'
                    ? `Datasets (${matchingDatasets.length})`
                    : GROUP_LABELS[item.group];
                const checked = isChecked(item);
                const busy = pending.has(pendingKey(item));
                const tableCount = item.kind === 'dataset' ? datasetTableCount(item.id) : undefined;
                return (
                  <li key={item.kind === 'dataset' ? `d:${item.id}` : `o:${item.fqn}`}>
                    {header && (
                      <div className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                        {header}
                      </div>
                    )}
                    <label
                      role="option"
                      aria-selected={checked}
                      onMouseEnter={() => setActive(idx)}
                      className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-xs cursor-pointer ${
                        idx === active ? 'bg-blue-50' : checked ? 'bg-blue-50/40' : 'hover:bg-gray-50'
                      }`}
                    >
                      {busy ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-600 shrink-0" />
                      ) : (
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggle(item)}
                          className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600 focus:ring-blue-500 shrink-0"
                        />
                      )}
                      {item.kind === 'dataset' ? (
                        <>
                          <Layers className="w-3.5 h-3.5 text-blue-600 shrink-0" />
                          <span className="font-medium text-gray-900 truncate">{item.id}</span>
                          {item.scope && <span className="text-gray-400 font-mono truncate">{item.scope}</span>}
                          <span className="ml-auto text-[11px] text-gray-500 shrink-0">
                            {tableCount !== undefined
                              ? `${tableCount} table${tableCount === 1 ? '' : 's'}`
                              : 'adds all its tables'}
                          </span>
                        </>
                      ) : (
                        <>
                          {item.objectType === 'CATALOG' ? (
                            <Database className="w-3.5 h-3.5 text-indigo-500 shrink-0" />
                          ) : item.objectType === 'SCHEMA' ? (
                            <FolderTree className="w-3.5 h-3.5 text-indigo-400 shrink-0" />
                          ) : (
                            <Table2 className="w-3.5 h-3.5 text-gray-400 shrink-0" />
                          )}
                          <span className="font-mono text-gray-900 truncate">{item.fqn}</span>
                          <span className="text-[10px] uppercase text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded shrink-0">
                            {item.inCache ? item.type : 'Unverified'}
                          </span>
                          {item.tagCount > 0 && (
                            <span className="ml-auto text-[11px] text-gray-400 shrink-0">
                              {item.tagCount} tag{item.tagCount === 1 ? '' : 's'}
                            </span>
                          )}
                        </>
                      )}
                    </label>
                    {item.kind === 'dataset' && idx === datasetCount - 1 && hiddenDatasets > 0 && (
                      <button
                        type="button"
                        onClick={() => setAllDatasets(true)}
                        className="w-full text-left px-3 py-1.5 pl-9 text-[11px] font-medium text-blue-700 hover:bg-gray-50"
                      >
                        Show {hiddenDatasets} more dataset{hiddenDatasets === 1 ? '' : 's'}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          <div className="flex items-center justify-between gap-3 border-t border-gray-100 bg-gray-50/70 px-3 py-2 text-[11px] text-gray-600">
            <span>
              <strong className="text-gray-900">{listSize}</strong> in your list
              {totalObjects > shownObjects && ` · showing ${shownObjects} of ${totalObjects} matches`}
              {q && result?.truncated && ' — narrow the search to see everything'}
            </span>
            <span className="flex items-center gap-3">
              {notYetAdded.length > 1 && (
                <button type="button" onClick={selectAll} className="font-semibold text-blue-700 hover:text-blue-900">
                  Select all {notYetAdded.length} matches
                  {isPattern ? ' ↵' : ''}
                </button>
              )}
              <button
                type="button"
                onClick={done}
                className="rounded-md bg-blue-600 px-3 py-1 font-semibold text-white hover:bg-blue-700"
              >
                Done
              </button>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
