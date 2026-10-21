import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { MoreHorizontal, Search, SlidersHorizontal, X } from 'lucide-react';
import { getObject, searchObjects } from '../../../services/metadataApi';
import { RELATION_TYPES, type ObjectSummary, type SearchQuery, type SecurableType } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { EmptyState, ErrorBanner, Notice, Pager, Skeleton } from '../../rbac/ui';
import { activeFilterCount, useBrowseFilters, type SearchWhat } from './browseFilters';
import { BrowseSwitch } from './BrowseSwitch';
import { BulkEditDialog } from './BulkEditDialog';
import { useMetadata } from './context';
import { DeclareObjectDialog } from './DeclareObjectDialog';
import { EditManyDialog } from './EditManyDialog';
import { errorMessage, plural, splitSearch } from './format';
import { Breadcrumb, ObjectHeader } from './ObjectHeader';
import { ObjectList } from './ObjectList';
import { adminPath } from './paths';
import { MenuItem, Popover } from './Popover';
import { seedFromSummary } from './staging';
import { useAsync, useDebounced } from './useAsync';

const PAGE_SIZE = 50;
const LIST_SIZE = 500;

const WHAT_TYPES: Record<SearchWhat, { types: SecurableType[]; columns: boolean }> = {
  tables: { types: RELATION_TYPES, columns: false },
  columns: { types: ['column'], columns: true },
  everything: { types: [], columns: true },
};

type Dialog = 'pattern' | 'declare' | 'many' | null;

/** A schema (or catalog) known only from the tables counted in it: its own row hasn't been read yet. */
function impliedRow(catalog: string | undefined, value: string): ObjectSummary {
  const name = catalog ? `${catalog}.${value}` : value;
  return {
    fqn_key: name.toLowerCase(),
    name,
    securable_type: catalog ? 'schema' : 'catalog',
    catalog: catalog ?? value,
    schema_name: catalog ? value : null,
    managed: false,
    tags: {},
    drift: false,
  };
}

/** All catalogs, one catalog's schemas, or one schema's tables, with search and filters scoped to where you are. */
export function BrowsePage() {
  const { catalog, schema } = useParams<{ catalog?: string; schema?: string }>();
  const { refreshToken, config, isAdmin } = useMetadata();
  const filters = useBrowseFilters();
  const [text, setText] = useState('');
  const q = useDebounced(text, 300);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Record<string, ObjectSummary>>({});
  const [dialog, setDialog] = useState<Dialog>(null);
  const searching = !!q.trim() || activeFilterCount(filters) > 0;
  const scopeName = schema ? `${catalog}.${schema}` : catalog;

  const query = useMemo<SearchQuery>(() => {
    const scope: SearchQuery = { include_unmanaged: true, sort: 'name', ...(schema ? { schemas: [`${catalog}.${schema}`] } : catalog ? { catalogs: [catalog] } : {}) };
    if (searching) {
      const { q: words, tags } = splitSearch(q);
      const what = WHAT_TYPES[filters.what];
      return {
        ...scope,
        q: words || undefined,
        tags: [...tags, ...(filters.tagKey ? [filters.tagKey] : [])],
        securable_types: what.types,
        include_columns: what.columns,
        drift: filters.outOfSync || undefined,
        editable_only: filters.editableOnly || undefined,
        environments: filters.environment ? [filters.environment] : undefined,
        page,
        page_size: PAGE_SIZE,
      };
    }
    if (schema) return { ...scope, securable_types: RELATION_TYPES, page, page_size: PAGE_SIZE };
    if (catalog) return { ...scope, securable_types: ['schema'], page, page_size: LIST_SIZE };
    return { ...scope, securable_types: ['catalog'], page, page_size: LIST_SIZE };
  }, [catalog, schema, searching, q, filters, page]);

  const load = useMemo(() => () => searchObjects(query), [query]);
  const { data, error, loading, reload } = useAsync(load, { keepPrevious: true, token: refreshToken });

  // How many tables each row holds, from one count-only search.
  const countsLoad = useMemo(
    () => (schema || searching ? null : () => searchObjects({ include_unmanaged: true, securable_types: RELATION_TYPES, page_size: 1, ...(catalog ? { catalogs: [catalog] } : {}) })),
    [catalog, schema, searching],
  );
  const { data: countsData } = useAsync(countsLoad, { token: refreshToken });
  const counts = useMemo(() => {
    const facet = countsData?.facets[catalog ? 'schema' : 'catalog'];
    return facet ? Object.fromEntries(facet.map((f) => [f.value, f.count])) : undefined;
  }, [countsData, catalog]);

  const items = useMemo(() => {
    if (!data || searching || schema || !counts) return data?.items ?? [];
    const listed = new Set(data.items.map((s) => (catalog ? s.schema_name ?? '' : s.catalog).toLowerCase()));
    const implied = Object.keys(counts).filter((v) => !listed.has(v.toLowerCase())).map((v) => impliedRow(catalog, v));
    return implied.length ? [...data.items, ...implied].sort((a, b) => a.name.localeCompare(b.name)) : data.items;
  }, [data, searching, schema, counts, catalog]);
  const unread = config.unread_catalogs ?? [];
  const unreadHere = catalog ? unread.filter((c) => c === catalog.toLowerCase()) : unread;

  const headerLoad = useMemo(() => (scopeName ? () => getObject(scopeName) : null), [scopeName]);
  const header = useAsync(headerLoad, { token: refreshToken });

  const setSearch = (value: string) => {
    setText(value);
    setPage(1);
  };
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
      (data?.items ?? []).forEach((s) => (on ? (next[s.fqn_key] = s) : delete next[s.fqn_key]));
      return next;
    });
  const selectedList = Object.values(selected);
  const selectable = !!schema || searching;
  const level = schema ? 'table' : catalog ? 'schema' : 'catalog';

  return (
    <div className="space-y-4">
      {catalog ? <Breadcrumb catalog={catalog} schema={schema} /> : <BrowseSwitch />}
      {scopeName && header.data && <ObjectHeader detail={header.data} title={schema ?? catalog ?? ''} />}
      {scopeName && !header.data && header.loading && <Skeleton rows={2} />}

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-64 flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden="true" />
          <Input
            value={text}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={scopeName ? `Search in ${scopeName}` : 'Search every catalog for a table, column or description'}
            aria-label="Search"
            className="pl-9 pr-8"
          />
          {text && (
            <button type="button" onClick={() => setSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700" aria-label="Clear the search">
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
        <FiltersButton onChange={() => setPage(1)} />
        <Popover label={<><MoreHorizontal className="h-4 w-4" /> More</>} ariaLabel="More actions" align="right">
          {(close) => (
            <>
              <MenuItem
                onClick={() => {
                  close();
                  setDialog('pattern');
                }}
                hint="For example every table in sales_dev.bronze"
              >
                Edit many tables by name
              </MenuItem>
              <MenuItem
                onClick={() => {
                  close();
                  setDialog('declare');
                }}
                hint="Tags are applied as soon as it’s created"
              >
                Prepare tags for a table that doesn’t exist yet
              </MenuItem>
            </>
          )}
        </Popover>
      </div>

      {selectedList.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-primary/30 bg-primary/5 px-4 py-2 text-sm">
          <span className="font-medium text-gray-900">{selectedList.length} selected</span>
          <Button size="sm" onClick={() => setDialog('many')}>
            {selectedList.length === 1 ? 'Edit tags and description' : `Edit all ${selectedList.length}`}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected({})}>
            Clear selection
          </Button>
        </div>
      )}

      {unreadHere.length > 0 && (
        <Notice kind="warn" title={catalog ? 'This catalog hasn’t been fully read' : 'Some catalogs haven’t been fully read'}>
          The last full check couldn’t read {catalog ? 'it' : unreadHere.join(', ')} from Unity Catalog, so some schemas and tables may be
          missing here. Tables created or changed since then still show up.{' '}
          {isAdmin ? (
            <Link to={adminPath} className="font-medium underline">
              See why under Admin → Sync history
            </Link>
          ) : (
            'An admin can see why under Admin → Sync history.'
          )}
        </Notice>
      )}

      {error ? <ErrorBanner message={errorMessage(error, 'Couldn’t load this list')} onRetry={reload} /> : null}
      {!data && loading ? (
        <Skeleton rows={8} />
      ) : data && items.length === 0 ? (
        <EmptyState title={searching ? 'Nothing matches' : `No ${level}s here`}>
          {searching
            ? 'Try fewer words, or clear the filters.'
            : level === 'catalog'
              ? 'No catalogs have been read from Unity Catalog yet. An admin can run a check from the Admin page.'
              : `Unity Catalog hasn’t reported any ${level}s here yet.`}
        </EmptyState>
      ) : data ? (
        <div className={loading ? 'space-y-2 opacity-60' : 'space-y-2'}>
          {searching && <p className="text-sm text-gray-500">{plural(data.total, 'result')}{scopeName ? ` in ${scopeName}` : ''}</p>}
          <ObjectList
            items={items}
            showPath={searching}
            counts={searching || schema ? undefined : counts}
            countLabel="Tables and views"
            selected={selectable ? selected : undefined}
            onToggle={selectable ? toggle : undefined}
            onToggleAll={selectable ? toggleAll : undefined}
          />
          <Pager offset={(page - 1) * data.page_size} limit={data.page_size} total={data.total} onChange={(o) => setPage(Math.floor(o / data.page_size) + 1)} />
        </div>
      ) : null}

      {dialog === 'pattern' && <BulkEditDialog initialPattern={schema ? `${catalog}.${schema}.*` : catalog ? `${catalog}.*.*` : undefined} onClose={() => setDialog(null)} />}
      {dialog === 'declare' && <DeclareObjectDialog onClose={() => setDialog(null)} />}
      {dialog === 'many' && (
        <EditManyDialog
          seeds={selectedList.map(seedFromSummary)}
          onClose={() => {
            setDialog(null);
            setSelected({});
          }}
        />
      )}
    </div>
  );
}

function FiltersButton({ onChange }: { onChange: () => void }) {
  const { config, keys } = useMetadata();
  const f = useBrowseFilters();
  const count = activeFilterCount(f);
  const update: typeof f.set = (patch) => {
    f.set(patch);
    onChange();
  };
  const tagKeys = keys.filter((k) => !k.exempt);
  return (
    <Popover
      label={
        <>
          <SlidersHorizontal className="h-4 w-4" /> Filters
          {count > 0 && <span className="rounded-full bg-primary px-1.5 text-xs text-white">{count}</span>}
        </>
      }
      ariaLabel="Filters"
      align="right"
      className="w-80 space-y-4 p-4"
    >
      <fieldset className="space-y-1.5">
        <legend className="mb-1 text-xs font-medium text-gray-500">When you search, look for</legend>
        {(
          [
            ['tables', 'Tables and views'],
            ['columns', 'Columns'],
            ['everything', 'Everything: catalogs, schemas, tables and columns'],
          ] as [SearchWhat, string][]
        ).map(([value, label]) => (
          <label key={value} className="flex items-center gap-2 text-sm text-gray-800">
            <input type="radio" name="metadata-search-what" checked={f.what === value} onChange={() => update({ what: value })} className="h-3.5 w-3.5 text-primary focus:ring-primary" />
            {label}
          </label>
        ))}
      </fieldset>
      <div className="space-y-1.5">
        <div className="text-xs font-medium text-gray-500">Only show</div>
        <label className="flex items-start gap-2 text-sm text-gray-800">
          <input type="checkbox" checked={f.outOfSync} onChange={(e) => update({ outOfSync: e.target.checked })} className="mt-0.5 h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary" />
          <span>
            Out of sync with Unity Catalog
            <span className="block text-xs text-gray-500">Someone changed it in Unity Catalog directly, or a save hasn’t reached it yet.</span>
          </span>
        </label>
        <label className="flex items-start gap-2 text-sm text-gray-800">
          <input type="checkbox" checked={f.editableOnly} onChange={(e) => update({ editableOnly: e.target.checked })} className="mt-0.5 h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary" />
          <span>
            Things I can change without approval
            <span className="block text-xs text-gray-500">Everything else can still be changed, with an approval.</span>
          </span>
        </label>
      </div>
      <label className="block space-y-1">
        <span className="text-xs font-medium text-gray-500">Has this tag</span>
        <select value={f.tagKey} onChange={(e) => update({ tagKey: e.target.value })} className="h-9 w-full rounded-md border border-gray-300 bg-white px-2 text-sm">
          <option value="">Any or none</option>
          {tagKeys.map((k) => (
            <option key={k.key} value={k.key}>
              {k.key}
            </option>
          ))}
        </select>
      </label>
      {config.environments.length > 1 && (
        <label className="block space-y-1">
          <span className="text-xs font-medium text-gray-500">Environment</span>
          <select value={f.environment} onChange={(e) => update({ environment: e.target.value })} className="h-9 w-full rounded-md border border-gray-300 bg-white px-2 text-sm">
            <option value="">All</option>
            {config.environments.map((e) => (
              <option key={e.key} value={e.key}>
                {e.key}
              </option>
            ))}
          </select>
        </label>
      )}
      <p className="text-xs text-gray-500">
        Tip: type <code>pii=*</code> to find anything tagged pii, or <code>pii=email</code> for one value.
      </p>
      {count > 0 && (
        <Button
          size="sm"
          variant="outline"
          className="w-full"
          onClick={() => {
            f.reset();
            onChange();
          }}
        >
          Clear filters
        </Button>
      )}
    </Popover>
  );
}
