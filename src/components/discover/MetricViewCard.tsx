import {
  TrendingUp,
  ShieldCheck,
  ExternalLink,
  ChevronRight,
  Database,
  LayoutDashboard,
  Check,
} from 'lucide-react';
import type { DataAsset, MetricKpi } from '../../services/api';
import { catalogExplorerUrl } from '../../lib/databricksLinks';
import { useBrandingStore } from '../../stores/brandingStore';

interface MetricViewCardProps {
  asset: DataAsset;
  isSelected?: boolean;
  onSelect: (asset: DataAsset) => void;
  onRequestAccess?: (asset: DataAsset) => void;
}

export function MetricViewCard({ asset, isSelected = false, onSelect }: MetricViewCardProps) {
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

  const kpis: MetricKpi[] = asset.kpis || [
    {
      name: 'Forecast Accuracy',
      value: '94.2%',
      trend: '+1.5%',
      formula: '1 - ABS(actual_qty - forecast_qty) / actual_qty',
      aggregation: 'AVG',
    },
    {
      name: 'Attainment',
      value: '98.2%',
      trend: '+1.0%',
      formula: 'actual_qty / planned_qty',
      aggregation: 'RATIO',
    },
  ];

  const dashboardsCount = asset.downstream_dashboards?.length || 3;
  const tablesCount = asset.upstream_tables?.length || 3;

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
        {/* Top Badges */}
        <div className="flex items-center justify-between gap-2 mb-3">
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                isSelected
                  ? 'bg-primary text-white'
                  : 'bg-primary/10 text-primary border border-primary/20'
              }`}
            >
              <TrendingUp className="w-3 h-3" />
              Metric View
            </span>

            {asset.certified && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-800">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
                Certified
              </span>
            )}

            {isSelected && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold bg-slate-900 text-white shadow-2xs">
                <Check className="w-3 h-3 stroke-[3]" /> Viewing Details
              </span>
            )}
          </div>

          {catalogUrl && (
            <a
              href={catalogUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="text-slate-400 hover:text-slate-600 p-1 rounded hover:bg-slate-100 transition-colors"
              title="Open in Unity Catalog Explorer"
            >
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          )}
        </div>

        {/* Title & Description */}
        <h4 className="text-base font-bold text-slate-900 group-hover:text-primary transition-colors capitalize mb-1">
          {cleanName}
        </h4>
        <div className="text-[11px] font-mono text-slate-400 mb-2 truncate">
          {asset.catalog}.{asset.schema_name}.{asset.table_name}
        </div>

        <p className="text-xs text-slate-600 line-clamp-2 mb-4 leading-relaxed">
          {asset.description ||
            `Governed business metrics and semantic dimensions for ${asset.subdomain || asset.domain}.`}
        </p>

        {/* Exposed KPIs Section */}
        <div className="mb-4">
          <div className="flex items-center justify-between text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-2">
            <span>Exposed KPIs ({kpis.length})</span>
            {kpis[0]?.dimensions && (
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

                {kpi.formula && (
                  <div className="mt-1 text-[9.5px] font-mono text-slate-400 truncate" title={kpi.formula}>
                    {kpi.formula}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Footer Details */}
      <div className="pt-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1 font-medium text-slate-600">
            <LayoutDashboard className="w-3 h-3 text-amber-500" />
            {dashboardsCount} Dashboards & Apps
          </span>
          <span className="text-slate-300">•</span>
          <span className="inline-flex items-center gap-1 text-slate-500">
            <Database className="w-3 h-3 text-slate-400" />
            {tablesCount} Tables
          </span>
        </div>

        <div className="inline-flex items-center gap-1 text-primary font-semibold group-hover:translate-x-0.5 transition-transform">
          <span>{isSelected ? 'Viewing details ↓' : 'Inspect KPIs & Lineage'}</span>
          <ChevronRight className="w-3.5 h-3.5" />
        </div>
      </div>
    </div>
  );
}
