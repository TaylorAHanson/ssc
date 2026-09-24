import { Database, TrendingUp, X } from 'lucide-react';
import type { DataAsset } from '../../services/api';
import { MetricViewCard } from './MetricViewCard';
import { AssetCard } from './AssetCard';
import { Pagination } from './Pagination';
import { usePagination } from './usePagination';

interface DiscoverSearchResultsProps {
  term: string;
  metricViews: DataAsset[];
  assets: DataAsset[];
  workspaceUrl: string;
  selectedAssetId?: string | null;
  onOpenMetricView: (mv: DataAsset) => void;
  onOpenAsset: (asset: DataAsset) => void;
  onClear: () => void;
  /** Legacy dashboard names matching the search, per metric view. */
  replacesFor?: (metricViewId: string) => string[];
}

// Cards per page: 4 rows of the 3-column grid, same as the domain view.
const PAGE_SIZE = 12;
const GRID = 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4';

function plural(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

const location = (a: DataAsset) => [a.domain, a.subdomain].filter(Boolean).join(' › ') || undefined;

/** Catalog-wide search results shown on the Discover landing page in place of the domain picker. */
export function DiscoverSearchResults({
  term,
  metricViews,
  assets,
  workspaceUrl,
  selectedAssetId = null,
  onOpenMetricView,
  onOpenAsset,
  onClear,
  replacesFor,
}: DiscoverSearchResultsProps) {
  const mvPages = usePagination(metricViews, PAGE_SIZE, `${term}|${metricViews.length}`);
  const assetPages = usePagination(assets, PAGE_SIZE, `${term}|${assets.length}`);
  const total = metricViews.length + assets.length;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-slate-600">
          {plural(total, 'result')} for <span className="font-semibold text-slate-900">“{term}”</span>
        </p>
        <button
          type="button"
          onClick={onClear}
          className="inline-flex items-center gap-1 text-xs font-semibold text-slate-500 hover:text-slate-800 cursor-pointer"
        >
          <X className="w-3.5 h-3.5" /> Clear search and browse domains
        </button>
      </div>

      {total === 0 && (
        <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-600">
          Nothing in the catalog matches “{term}”. Try another word, press <strong>Ask AI</strong>, or clear the
          search to browse by domain.
        </div>
      )}

      {metricViews.length > 0 && (
        <section className="space-y-3 scroll-mt-28">
          <h3 className="flex items-center gap-2 text-sm font-bold text-primary">
            <TrendingUp className="w-4 h-4" /> Metric Views
            <span className="text-slate-400 font-semibold">({metricViews.length})</span>
          </h3>
          <div className={GRID}>
            {mvPages.pageItems.map((mv) => (
              <MetricViewCard
                key={mv.id}
                asset={mv}
                context={location(mv)}
                replaces={replacesFor?.(mv.id)}
                onSelect={onOpenMetricView}
              />
            ))}
          </div>
          <Pagination {...mvPages} onPageChange={mvPages.setPage} />
        </section>
      )}

      {assets.length > 0 && (
        <section className="space-y-3 scroll-mt-28">
          <h3 className="flex items-center gap-2 text-sm font-bold text-indigo-700">
            <Database className="w-4 h-4" /> Tables, Dashboards & More
            <span className="text-slate-400 font-semibold">({assets.length})</span>
          </h3>
          <div className={GRID}>
            {assetPages.pageItems.map((ds) => (
              <AssetCard
                key={`${ds.type}:${ds.id}`}
                asset={ds}
                workspaceUrl={workspaceUrl}
                context={location(ds)}
                isSelected={selectedAssetId === ds.id}
                onSelect={onOpenAsset}
              />
            ))}
          </div>
          <Pagination {...assetPages} onPageChange={assetPages.setPage} />
        </section>
      )}
    </div>
  );
}
