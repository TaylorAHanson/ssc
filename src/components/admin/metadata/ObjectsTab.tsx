import { useCallback, useMemo, useState } from 'react';
import { Download, FilePlus2, Filter, Layers, Upload } from 'lucide-react';
import { searchObjects } from '../../../services/metadataApi';
import type { ObjectSummary, SearchQuery } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { EmptyState, ErrorBanner, Pager, Skeleton } from '../../rbac/ui';
import { AdoptDialog } from './AdoptDialog';
import { BulkEditDialog } from './BulkEditDialog';
import { useMetadata } from './context';
import { DeclareObjectDialog } from './DeclareObjectDialog';
import { FacetSidebar } from './FacetSidebar';
import { errorMessage, plural } from './format';
import { ImportDialog } from './ImportDialog';
import { activeFilterCount, toSearchQuery, useObjectsQuery } from './objectsQuery';
import { ObjectsTable } from './ObjectsTable';
import { SearchBar } from './SearchBar';
import { seedFromSummary, useStagingStore } from './staging';
import { useAsync, useDebounced } from './useAsync';

type Dialog = 'declare' | 'adopt' | 'import' | 'bulk' | null;

export function ObjectsTab() {
  const { config, isAdmin, refreshToken, openObject } = useMetadata();
  const query = useObjectsQuery();
  const text = useDebounced(query.text, 300);
  // Keyed by value so typing (before the debounce) and unrelated store updates don't refetch.
  const searchKey = JSON.stringify(toSearchQuery(query, text));
  const load = useMemo(() => {
    const q = JSON.parse(searchKey) as SearchQuery;
    return () => searchObjects(q);
  }, [searchKey]);
  const { data, error, loading, reload } = useAsync(load, { keepPrevious: true, token: refreshToken });

  const [selection, setSelection] = useState<Record<string, ObjectSummary>>({});
  const [showFilters, setShowFilters] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const stage = useStagingStore((s) => s.stage);
  const order = useStagingStore((s) => s.order);
  const stagedKeys = useMemo(() => new Set(order), [order]);
  const selected = Object.values(selection);
  const filters = activeFilterCount(query);

  const onOpen = useCallback((s: ObjectSummary) => openObject(s.name, s.column), [openObject]);
  const onStage = useCallback((s: ObjectSummary) => stage([seedFromSummary(s)]), [stage]);

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="space-y-4">
      <div className="flex gap-6">
        <div className={showFilters ? 'w-60 shrink-0' : 'hidden w-60 shrink-0 lg:block'}>
          <FacetSidebar facets={data?.facets} />
        </div>
        <div className="min-w-0 flex-1 space-y-3">
          <SearchBar />
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" className="gap-1.5 lg:hidden" onClick={() => setShowFilters(!showFilters)} aria-expanded={showFilters}>
              <Filter className="h-3.5 w-3.5" /> Filters{filters ? ` (${filters})` : ''}
            </Button>
            <span className="text-sm text-gray-600" aria-live="polite">
              {data ? `${plural(total, 'object')}${data.took_ms !== null && data.took_ms !== undefined ? ` · ${data.took_ms} ms` : ''}` : ''}
            </span>
            {filters > 0 && (
              <button type="button" onClick={query.reset} className="text-xs text-primary hover:underline">
                Clear all filters
              </button>
            )}
            <div className="ml-auto flex flex-wrap gap-2">
              <Button size="sm" variant="outline" className="gap-1.5" onClick={() => setDialog('bulk')}>
                <Layers className="h-3.5 w-3.5" /> Bulk edit
              </Button>
              <Button size="sm" variant="outline" className="gap-1.5" onClick={() => setDialog('declare')}>
                <FilePlus2 className="h-3.5 w-3.5" /> Declare a new object
              </Button>
              {isAdmin && (
                <>
                  <Button size="sm" variant="outline" className="gap-1.5" onClick={() => setDialog('adopt')}>
                    <Download className="h-3.5 w-3.5" /> Adopt current state
                  </Button>
                  <Button size="sm" variant="outline" className="gap-1.5" onClick={() => setDialog('import')}>
                    <Upload className="h-3.5 w-3.5" /> Import CSV or JSON
                  </Button>
                </>
              )}
            </div>
          </div>

          {selected.length > 0 && (
            <div className="flex flex-wrap items-center gap-3 rounded-md border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm" role="region" aria-label="Selection">
              <span className="font-medium text-indigo-900">{plural(selected.length, 'object')} selected</span>
              <Button
                size="sm"
                onClick={() => {
                  stage(selected.map(seedFromSummary));
                  setSelection({});
                }}
              >
                Stage for editing
              </Button>
              <button type="button" onClick={() => setSelection({})} className="text-xs text-indigo-800 hover:underline">
                Clear selection
              </button>
              {selected.length > config.max_objects_per_change && (
                <span className="text-xs text-amber-800">
                  One change can cover at most {config.max_objects_per_change.toLocaleString()} objects.
                </span>
              )}
            </div>
          )}

          {error ? <ErrorBanner message={errorMessage(error, 'Couldn’t search objects')} onRetry={reload} /> : null}
          {!data && loading ? (
            <Skeleton rows={8} />
          ) : items.length === 0 && !loading ? (
            filters > 0 ? (
              <EmptyState title="No objects match">
                Try fewer filters, or{' '}
                <button type="button" onClick={query.reset} className="text-primary hover:underline">
                  clear them all
                </button>
                . Turn on “Include unmanaged” to search objects nobody manages yet.
              </EmptyState>
            ) : (
              <EmptyState title="No managed objects yet">
                {isAdmin ? 'Use “Adopt current state” to start managing what’s already in Unity Catalog.' : 'Ask an admin to adopt the catalogs you work with.'}
              </EmptyState>
            )
          ) : (
            <div className={loading ? 'opacity-60 transition-opacity' : undefined} aria-busy={loading}>
              <ObjectsTable
                items={items}
                sort={query.sort}
                order={query.order}
                onSort={(sort, order) => query.update({ sort, order })}
                selection={selection}
                onSelectionChange={setSelection}
                stagedKeys={stagedKeys}
                exemptKeys={config.exempt_keys}
                onOpen={onOpen}
                onStage={onStage}
              />
            </div>
          )}
          <Pager
            offset={(query.page - 1) * query.page_size}
            limit={query.page_size}
            total={total}
            onChange={(offset) => query.update({ page: Math.floor(offset / query.page_size) + 1 })}
          />
        </div>
      </div>
      {dialog === 'declare' && <DeclareObjectDialog onClose={() => setDialog(null)} />}
      {dialog === 'adopt' && <AdoptDialog onClose={() => setDialog(null)} />}
      {dialog === 'import' && <ImportDialog onClose={() => setDialog(null)} />}
      {dialog === 'bulk' && <BulkEditDialog onClose={() => setDialog(null)} initialPattern={/[*?]/.test(query.text) ? query.text.trim() : ''} />}
    </div>
  );
}
