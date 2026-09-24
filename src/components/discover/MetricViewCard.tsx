import {
  ShieldCheck,
  ExternalLink,
  ChevronRight,
  Database,
  LayoutDashboard,
} from 'lucide-react';
import type { DataAsset, MetricKpi, MetricViewDefinition } from '../../services/api';
import { catalogExplorerUrl } from '../../lib/databricksLinks';
import { useBrandingStore } from '../../stores/brandingStore';
import { useMetricViewDefinition } from './useMetricViewDefinition';

/** Why a card has no KPIs to show, once its definition has been read. */
function noKpisMessage(definition: MetricViewDefinition): string {
  if (definition.available) return 'This metric view defines no KPIs.';
  switch (definition.reason) {
    case 'permission_denied':
      return "You don't have access to this metric view's KPIs.";
    case 'no_obo':
      return 'Open the app from Databricks to see KPIs with your permissions.';
    default:
      return "Couldn't load KPIs.";
  }
}

interface MetricViewCardProps {
  asset: DataAsset;
  isSelected?: boolean;
  onSelect: (asset: DataAsset) => void;
  onRequestAccess?: (asset: DataAsset) => void;
  /** Optional line above the title, e.g. "Finance › Planning & Budgeting" in search results. */
  context?: string;
}

export function MetricViewCard({ asset, isSelected = false, onSelect, context }: MetricViewCardProps) {
  const databricksWorkspaceUrl = useBrandingStore((s) => s.databricksWorkspaceUrl);
  const catalogUrl = catalogExplorerUrl(
    databricksWorkspaceUrl,
    asset.catalog,
    asset.schema_name,
    asset.table_name,
  );

  const cleanName = asset.table_name
    .replace(/^(metric_|sem_)/, '')
    .replace(/_metric_view$/, '')
    .replace(/_/g, ' ');

  // KPIs and source tables come from the definition, read as the user (batched
  // across the cards on screen) — the shared catalog cache doesn't hold them.
  const definition = useMetricViewDefinition(asset.id);
  const kpis: MetricKpi[] = definition?.kpis ?? [];
  const dashboardsCount = asset.downstream_dashboards?.length ?? 0;
  const tablesCount = definition?.available ? definition.upstream_tables.length : undefined;

  return (
    <div
      onClick={() => onSelect(asset)}
      className={`group relative rounded-2xl p-5 transition-all cursor-pointer flex flex-col justify-between ${
        isSelected
          ? 'bg-primary/5 border-2 border-primary shadow-md ring-2 ring-primary/20'
          : 'bg-white border border-slate-200 shadow-xs hover:shadow-md hover:border-primary/40'
      }`}
    >
      <div>
        {catalogUrl && (
          <a
            href={catalogUrl}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="absolute top-4 right-4 text-slate-400 hover:text-slate-600 p-1 rounded hover:bg-slate-100 transition-colors"
            title="Open in Catalog Explorer"
          >
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        )}

        {asset.certified && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 mb-2 rounded-full text-xs font-medium bg-emerald-100 text-emerald-800">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
            Certified
          </span>
        )}

        {/* Title & Description */}
        {context && <div className="text-[11px] font-semibold text-slate-500 mb-1 pr-6 truncate">{context}</div>}
        <h4 className="text-base font-bold text-slate-900 group-hover:text-primary transition-colors capitalize mb-1 pr-6">
          {cleanName}
        </h4>
        <div className="text-[11px] font-mono text-slate-400 mb-2 truncate">
          {asset.catalog}.{asset.schema_name}.{asset.table_name}
        </div>

        {asset.description && (
          <p className="text-xs text-slate-600 line-clamp-2 mb-4 leading-relaxed">{asset.description}</p>
        )}

        {/* KPIs */}
        {!definition ? (
          <div className="mb-4 animate-pulse" aria-label="Loading KPIs">
            <div className="h-2.5 w-16 rounded bg-slate-200 mb-2" />
            <div className="grid grid-cols-2 gap-2">
              {[0, 1].map((i) => (
                <div key={i} className="h-9 rounded-xl bg-slate-100 border border-slate-200/70" />
              ))}
            </div>
          </div>
        ) : kpis.length === 0 ? (
          <p className="mb-4 text-[11px] text-slate-400 italic">{noKpisMessage(definition)}</p>
        ) : (
        <div className="mb-4">
          <div className="flex items-center justify-between text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-2">
            <span>KPIs ({kpis.length})</span>
            {kpis[0]?.dimensions && kpis[0].dimensions.length > 0 && (
              <span className="text-[10px] text-slate-400 font-normal lowercase truncate max-w-[140px]">
                by {kpis[0].dimensions.slice(0, 2).join(', ')}
              </span>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2">
            {kpis.slice(0, 4).map((kpi, idx) => (
              <div
                key={idx}
                className="bg-slate-50 hover:bg-slate-100/80 rounded-xl p-2.5 border border-slate-200/70 transition-colors flex flex-col justify-between"
                title={kpi.formula ? `Formula: ${kpi.formula}` : kpi.description || kpi.name}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[11px] font-semibold text-slate-700 truncate">{kpi.name}</span>
                  {kpi.aggregation && (
                    <span className="text-[9px] font-mono px-1 py-0.2 bg-slate-200/70 rounded text-slate-600 uppercase">
                      {kpi.aggregation}
                    </span>
                  )}
                </div>

                {(kpi.value || kpi.trend) && (
                <div className="flex items-baseline justify-between mt-1">
                  <span className="text-sm font-extrabold text-slate-900">{kpi.value}</span>
                  {kpi.trend && (
                    <span
                      className={`text-[10px] font-semibold ${
                        kpi.trend.startsWith('+')
                          ? 'text-emerald-600'
                          : kpi.trend.startsWith('-')
                          ? 'text-amber-600'
                          : 'text-slate-400'
                      }`}
                    >
                      {kpi.trend}
                    </span>
                  )}
                </div>
                )}

                {kpi.formula && (
                  <div className="mt-1 text-[9.5px] font-mono text-slate-400 truncate" title={kpi.formula}>
                    {kpi.formula}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
        )}
      </div>

      {/* Footer Details */}
      <div className="pt-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1 font-medium text-slate-600">
            <LayoutDashboard className="w-3 h-3 text-amber-500" />
            {dashboardsCount} {dashboardsCount === 1 ? 'dashboard' : 'dashboards'}
          </span>
          {tablesCount != null && (
            <>
              <span className="text-slate-300">•</span>
              <span className="inline-flex items-center gap-1 text-slate-500">
                <Database className="w-3 h-3 text-slate-400" />
                {tablesCount} {tablesCount === 1 ? 'source' : 'sources'}
              </span>
            </>
          )}
        </div>

        <div className="inline-flex items-center gap-1 text-primary font-semibold group-hover:translate-x-0.5 transition-transform">
          <span>{isSelected ? 'Viewing ↓' : 'Details'}</span>
          <ChevronRight className="w-3.5 h-3.5" />
        </div>
      </div>
    </div>
  );
}
