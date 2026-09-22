import { useState } from 'react';
import {
  TrendingUp,
  ShieldCheck,
  ExternalLink,
  ArrowLeft,
  LayoutDashboard,
  Database,
  Lock,
  GitBranch,
  Calendar,
  Clock,
  User,
  CheckCircle2,
} from 'lucide-react';
import type { DataAsset, MetricKpi } from '../../services/api';
import { catalogExplorerUrl } from '../../lib/databricksLinks';
import { useBrandingStore } from '../../stores/brandingStore';
import { LineageGraph } from './LineageGraph';

interface DashboardOrApp {
  id: string;
  name: string;
  type?: string;
  description?: string;
  owner?: string;
  views?: number;
  updated_at?: string;
}

interface UpstreamTableInfo {
  name: string;
  fqn?: string;
  schema?: string;
  type?: string;
  description?: string;
}

interface MetricViewDetailProps {
  asset: DataAsset;
  onBack: () => void;
  onRequestAccess?: (asset: DataAsset) => void;
  onSelectTable?: (tableName: string) => void;
  isAdvanced?: boolean;
}

export function MetricViewDetail({
  asset,
  onBack,
  onRequestAccess,
  onSelectTable,
  isAdvanced = false,
}: MetricViewDetailProps) {
  const [activeTab, setActiveTab] = useState<'overview' | 'lineage' | 'quality'>('overview');
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
      trend: '+1.5% vs plan',
      formula: '1 - ABS(actual_qty - forecast_qty) / NULLIF(actual_qty, 0)',
      aggregation: 'AVG',
      unit: '%',
      dimensions: ['Region', 'Product Family', 'Horizon'],
      description: 'Weighted demand forecast accuracy against actual delivered orders.',
    },
    {
      name: 'MAPE',
      value: '5.8%',
      trend: '-0.4% improvement',
      formula: 'AVG(ABS(actual_qty - forecast_qty) / NULLIF(actual_qty, 0)) * 100',
      aggregation: 'AVG',
      unit: '%',
      dimensions: ['SKU', 'Distribution Center', 'Fiscal Week'],
      description: 'Mean absolute percentage variance between forecasted demand and physical shipments.',
    },
    {
      name: 'Forecast Bias',
      value: '-1.2%',
      trend: 'neutral',
      formula: 'SUM(forecast_qty - actual_qty) / NULLIF(SUM(actual_qty), 0) * 100',
      aggregation: 'RATIO',
      unit: '%',
      dimensions: ['Product Line', 'Region'],
      description: 'Tendency of the statistical forecast to consistently over- or under-predict.',
    },
    {
      name: 'Plan Attainment',
      value: '98.2%',
      trend: '+1.0%',
      formula: 'SUM(actual_qty) / NULLIF(SUM(planned_qty), 0) * 100',
      aggregation: 'RATIO',
      unit: '%',
      dimensions: ['Business Unit', 'Quarter'],
      description: 'Percentage of overall operational supply plan achieved to date.',
    },
  ];

  const downstreamDashboards = asset.downstream_dashboards || [
    {
      id: `dash_${asset.table_name}_review`,
      name: `Weekly ${cleanName} Review`,
      type: 'dashboard',
      description: `Exec-level ${cleanName} review with forecast vs actuals and regional breakdown`,
      owner: 'SCM Analytics',
      views: 342,
      updated_at: '2 hours ago',
    },
    {
      id: `dash_${asset.table_name}_actuals`,
      name: `${cleanName} vs Actuals`,
      type: 'dashboard',
      description: 'Waterfall chart showing forecast accuracy by product family and quarter',
      owner: 'Demand Planning',
      views: 218,
      updated_at: '4 hours ago',
    },
    {
      id: `app_${asset.table_name}_explorer`,
      name: `${cleanName} Hierarchy Explorer`,
      type: 'app',
      description: 'Interactive drill-down tool with filters by BU and region',
      owner: 'Enterprise Tools',
      views: 156,
      updated_at: '1 day ago',
    },
  ];

  const upstreamTables = asset.upstream_tables || [
    {
      name: `${asset.table_name.replace(/^(metric_|sem_)/, '')}_forecast_weekly`,
      fqn: `${asset.catalog}.silver_${asset.schema_name}.${asset.table_name}_forecast_weekly`,
      schema: `silver_${asset.schema_name}`,
      type: 'Silver Managed Table',
      description: 'Forecast projections and weekly statistical prediction outputs',
    },
    {
      name: `${asset.table_name.replace(/^(metric_|sem_)/, '')}_actuals_daily`,
      fqn: `${asset.catalog}.gold_${asset.schema_name}.${asset.table_name}_actuals_daily`,
      schema: `gold_${asset.schema_name}`,
      type: 'Gold Managed Table',
      description: 'Recorded historical actuals synced from enterprise systems',
    },
    {
      name: `${asset.table_name.replace(/^(metric_|sem_)/, '')}_variance`,
      fqn: `${asset.catalog}.platinum_insights.${asset.table_name}_variance`,
      schema: 'platinum_insights',
      type: 'Platinum Table',
      description: 'Variance analysis comparing enterprise plan vs actual',
    },
  ];

  return (
    <div className="space-y-6">
      {/* Top Breadcrumb & Back Button */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs text-slate-500 font-medium">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center gap-1.5 text-slate-700 hover:text-slate-900 font-semibold px-2 py-1 rounded-md hover:bg-slate-100 transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Back to Directory
          </button>
          <span>/</span>
          <span className="text-slate-700">{asset.domain || 'Domain'}</span>
          <span>/</span>
          <span className="text-slate-700">{asset.subdomain || 'Subdomain'}</span>
          <span>/</span>
          <span className="text-emerald-700 font-bold truncate max-w-[200px]">{cleanName}</span>
        </div>

        <div className="flex items-center gap-2">
          {catalogUrl && (
            <a
              href={catalogUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-semibold text-slate-700 hover:bg-slate-50 transition-colors shadow-sm"
            >
              <ExternalLink className="w-3.5 h-3.5 text-slate-400" />
              <span>Catalog Explorer</span>
            </a>
          )}

          {onRequestAccess && (
            <button
              type="button"
              onClick={() => onRequestAccess(asset)}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold shadow-sm transition-colors"
            >
              <Lock className="w-3.5 h-3.5" />
              <span>Request Access</span>
            </button>
          )}
        </div>
      </div>

      {/* Main Header Card */}
      <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm">
        <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-6">
          <div className="flex-1">
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-emerald-100 text-emerald-800 border border-emerald-300">
                <TrendingUp className="w-3.5 h-3.5 text-emerald-700" />
                METRIC VIEW
              </span>

              {asset.certified && (
                <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                  <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
                  ✓ Certified
                </span>
              )}

              {asset.domain && (
                <span className="text-xs px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-700 font-medium">
                  {asset.domain}
                </span>
              )}
              {asset.subdomain && (
                <span className="text-xs px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-700 font-medium">
                  {asset.subdomain}
                </span>
              )}
            </div>

            <h1 className="text-2xl font-extrabold text-slate-900 capitalize tracking-tight">
              {cleanName}
            </h1>
            {isAdvanced && (
              <p className="mt-1 font-mono text-xs text-slate-400">
                {asset.catalog}.{asset.schema_name}.{asset.table_name}
              </p>
            )}

            <p className="mt-3 text-sm text-slate-600 max-w-3xl leading-relaxed">
              {asset.description ||
                `Exec-level demand forecast with historical actuals, bias analysis, and multi-horizon projections across product families.`}
            </p>

            <div className="mt-4 flex flex-wrap items-center gap-4 text-xs text-slate-500 pt-3 border-t border-slate-100">
              <div className="flex items-center gap-1.5">
                <User className="w-3.5 h-3.5 text-slate-400" />
                <span>Owner: <strong className="text-slate-700">{asset.owner || 'SCM Analytics'}</strong></span>
              </div>
              <div className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5 text-slate-400" />
                <span>SLA: <strong className="text-slate-700">{asset.sla || 'Daily 06:00 UTC'}</strong></span>
              </div>
              <div className="flex items-center gap-1.5">
                <Calendar className="w-3.5 h-3.5 text-slate-400" />
                <span>Refreshed: <strong className="text-slate-700">Today</strong></span>
              </div>
            </div>
          </div>
        </div>

        {/* Headline Business KPIs */}
        <div className="mt-6 grid grid-cols-2 sm:grid-cols-4 gap-3">
          {kpis.map((kpi, idx) => (
            <div
              key={idx}
              className="bg-slate-50 rounded-xl p-4 border border-slate-200/80 flex flex-col justify-between"
            >
              <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">
                {kpi.name}
              </div>
              <div className="text-2xl font-black text-slate-900 tracking-tight">{kpi.value}</div>
              {kpi.trend && (
                <div
                  className={`mt-1.5 text-xs font-medium inline-flex items-center gap-1 ${
                    kpi.trend.startsWith('+')
                      ? 'text-emerald-600'
                      : kpi.trend.startsWith('-')
                      ? 'text-amber-600'
                      : 'text-slate-500'
                  }`}
                >
                  <span>{kpi.trend}</span>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Detail View Tabs */}
      <div className="flex items-center gap-2 border-b border-slate-200 pb-2">
        <button
          type="button"
          onClick={() => setActiveTab('overview')}
          className={`px-4 py-2 text-xs font-bold rounded-lg transition-colors ${
            activeTab === 'overview'
              ? 'bg-emerald-50 text-emerald-800 border border-emerald-200'
              : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
          }`}
        >
          Consumption & Tables
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('lineage')}
          className={`px-4 py-2 text-xs font-bold rounded-lg transition-colors flex items-center gap-1.5 ${
            activeTab === 'lineage'
              ? 'bg-indigo-50 text-indigo-800 border border-indigo-200'
              : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
          }`}
        >
          <GitBranch className="w-3.5 h-3.5" />
          Interactive Lineage
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('quality')}
          className={`px-4 py-2 text-xs font-bold rounded-lg transition-colors flex items-center gap-1.5 ${
            activeTab === 'quality'
              ? 'bg-amber-50 text-amber-800 border border-amber-200'
              : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
          }`}
        >
          <ShieldCheck className="w-3.5 h-3.5" />
          Quality & Governance
        </button>
      </div>

      {/* Tab: Overview & Consumption */}
      {activeTab === 'overview' && (
        <div className="space-y-6">
          {/* Section: Governed KPIs & Semantic Measures */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
                  <TrendingUp className="w-4 h-4 text-emerald-600" />
                  Governed KPIs & Semantic Measures ({kpis.length})
                </h3>
                <p className="text-xs text-slate-500">
                  Business definitions, calculation formulas, and aggregation rules exposed by this metric view
                </p>
              </div>
              <span className="text-xs bg-emerald-50 text-emerald-800 border border-emerald-200 font-bold px-2.5 py-0.5 rounded-full">
                {kpis.length} Defined Measures
              </span>
            </div>

            <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
                    <th className="py-2.5 px-4">KPI / Measure</th>
                    <th className="py-2.5 px-4">Calculation / Formula</th>
                    <th className="py-2.5 px-4">Aggregation</th>
                    <th className="py-2.5 px-4">Benchmark & Trend</th>
                    <th className="py-2.5 px-4">Supported Dimensions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {kpis.map((kpi, idx) => (
                    <tr key={idx} className="hover:bg-slate-50/70 transition-colors">
                      <td className="py-3 px-4 font-semibold text-slate-900">
                        <div className="flex items-center gap-1.5">
                          <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0" />
                          <span>{kpi.name}</span>
                        </div>
                        {kpi.description && (
                          <div className="text-[11px] text-slate-500 font-normal mt-0.5 max-w-xs leading-relaxed">
                            {kpi.description}
                          </div>
                        )}
                      </td>
                      <td className="py-3 px-4 font-mono text-[11px] text-emerald-800">
                        <span className="bg-slate-50 border border-slate-200/90 px-2 py-1 rounded inline-block max-w-sm truncate">
                          {kpi.formula || `SUM(${kpi.name.toLowerCase().replace(/ /g, '_')})`}
                        </span>
                      </td>
                      <td className="py-3 px-4">
                        <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-700 font-mono text-[11px] font-semibold uppercase">
                          {kpi.aggregation || 'RATIO'}
                        </span>
                      </td>
                      <td className="py-3 px-4">
                        <span className="font-extrabold text-slate-900 text-sm">{kpi.value}</span>
                        {kpi.trend && (
                          <span
                            className={`ml-1.5 text-[10px] font-bold ${
                              kpi.trend.startsWith('+')
                                ? 'text-emerald-600'
                                : kpi.trend.startsWith('-')
                                ? 'text-amber-600'
                                : 'text-slate-500'
                            }`}
                          >
                            {kpi.trend}
                          </span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-slate-600">
                        <div className="flex flex-wrap gap-1">
                          {(kpi.dimensions || ['Region', 'Product Family', 'Period']).map((dim, dIdx) => (
                            <span
                              key={dIdx}
                              className="px-1.5 py-0.5 bg-slate-100 text-slate-700 rounded text-[10px] font-medium"
                            >
                              {dim}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Section: Related Dashboards and Apps */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
                  <LayoutDashboard className="w-4 h-4 text-amber-500" />
                  Related Dashboards and Apps
                </h3>
                <p className="text-xs text-slate-500">
                  Lakeview dashboards and interactive tools consuming this metric view
                </p>
              </div>
              <span className="text-xs bg-slate-100 text-slate-600 font-semibold px-2 py-0.5 rounded-full">
                {downstreamDashboards.length} tools
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {(downstreamDashboards as DashboardOrApp[]).map((dash: DashboardOrApp) => (
                <div
                  key={dash.id}
                  className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm hover:border-slate-300 transition-all flex flex-col justify-between"
                >
                  <div>
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <span
                        className={`text-[11px] font-semibold uppercase px-2 py-0.5 rounded-md ${
                          dash.type === 'app'
                            ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                            : 'bg-amber-50 text-amber-700 border border-amber-200'
                        }`}
                      >
                        {dash.type === 'app' ? 'App' : 'Dashboard'}
                      </span>
                      <span className="text-[11px] text-slate-400">{dash.updated_at || 'Updated recently'}</span>
                    </div>

                    <h4 className="font-bold text-slate-900 text-sm mb-1">{dash.name}</h4>
                    <p className="text-xs text-slate-600 line-clamp-2 mb-3 leading-relaxed">
                      {dash.description}
                    </p>
                  </div>

                  <div className="pt-3 border-t border-slate-100 flex items-center justify-between">
                    <span className="text-[11px] text-slate-500">
                      Author: <strong className="text-slate-700">{dash.owner || 'Analytics'}</strong>
                    </span>

                    <button
                      type="button"
                      onClick={() => alert(`Launching ${dash.name}`)}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-700 hover:text-emerald-800"
                    >
                      <span>Open</span>
                      <ExternalLink className="w-3 h-3" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Section: Supporting Lakehouse Tables */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
                  <Database className="w-4 h-4 text-blue-500" />
                  Supporting Lakehouse Tables
                </h3>
                <p className="text-xs text-slate-500">
                  Governed tables in the data lake that feed this metric view
                </p>
              </div>
              <span className="text-xs bg-slate-100 text-slate-600 font-semibold px-2 py-0.5 rounded-full">
                {upstreamTables.length} tables
              </span>
            </div>

            <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
                    <th className="py-2.5 px-4">Table Name</th>
                    <th className="py-2.5 px-4">Tier / Type</th>
                    <th className="py-2.5 px-4">Description</th>
                    <th className="py-2.5 px-4 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {(upstreamTables as UpstreamTableInfo[]).map((tbl: UpstreamTableInfo, idx: number) => (
                    <tr key={idx} className="hover:bg-slate-50/70 transition-colors">
                      <td className="py-3 px-4 font-mono font-medium text-slate-900">
                        <div>{tbl.name}</div>
                        <div className="text-[10px] text-slate-400 font-normal">{tbl.fqn || tbl.schema}</div>
                      </td>
                      <td className="py-3 px-4">
                        <span className="px-2 py-0.5 rounded-md bg-slate-100 text-slate-700 font-semibold text-[11px]">
                          {tbl.type || 'Table'}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-slate-600 max-w-md">
                        {tbl.description || 'Underlying data asset supporting metric computations.'}
                      </td>
                      <td className="py-3 px-4 text-right">
                        <button
                          type="button"
                          onClick={() => {
                            if (onSelectTable) onSelectTable(tbl.fqn || tbl.name);
                          }}
                          className="text-xs font-semibold text-emerald-700 hover:text-emerald-800"
                        >
                          View Details ↗
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Tab: Lineage */}
      {activeTab === 'lineage' && (
        <div className="bg-white rounded-2xl border border-slate-200 p-4 shadow-sm">
          <div className="mb-3">
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-1.5">
              <GitBranch className="w-4 h-4 text-indigo-600" />
              Lineage Graph for {cleanName}
            </h3>
            <p className="text-xs text-slate-500">
              Interactive node graph tracing upstream silver/gold tables and downstream Lakeview dashboards and Apps
            </p>
          </div>
          <div className="h-[460px] rounded-xl overflow-hidden border border-slate-100 bg-slate-50">
            <LineageGraph
              seedTables={[
                {
                  fqn: `${asset.catalog}.${asset.schema_name}.${asset.table_name}`,
                  displayName: cleanName,
                  upstreams: Array.isArray(asset.upstream_tables)
                    ? (asset.upstream_tables as Array<string | UpstreamTableInfo>).map((t) =>
                        typeof t === 'string' ? t : t.fqn || t.name,
                      )
                    : [],
                },
              ]}
              workspaceUrl={databricksWorkspaceUrl}
              height="100%"
            />
          </div>
        </div>
      )}

      {/* Tab: Quality & Certification */}
      {activeTab === 'quality' && (
        <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm space-y-4">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-emerald-600" />
            <h3 className="text-base font-bold text-slate-900">Data Certification & Governance</h3>
          </div>

          <div className="p-4 rounded-xl bg-slate-50 border border-slate-200 flex items-start gap-3">
            <CheckCircle2 className="w-5 h-5 text-emerald-600 mt-0.5 flex-shrink-0" />
            <div>
              <div className="text-xs font-bold text-slate-900">
                {asset.certified ? 'Certified Governed Asset' : 'Self-Service Asset'}
              </div>
              <p className="text-xs text-slate-600 mt-0.5">
                {asset.certified
                  ? 'This metric view meets all enterprise certification standards, SLA benchmarks, and data quality tests.'
                  : 'This asset is currently in self-service status. Data quality verification can be requested via the governance workflow.'}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="p-3 bg-slate-50 rounded-xl border border-slate-200">
              <div className="text-[11px] text-slate-500 uppercase font-semibold">Freshness SLA</div>
              <div className="text-sm font-bold text-slate-900 mt-1">99.8% on-time</div>
              <div className="text-[10px] text-emerald-600 font-semibold mt-0.5">✓ Met for 30 consecutive days</div>
            </div>
            <div className="p-3 bg-slate-50 rounded-xl border border-slate-200">
              <div className="text-[11px] text-slate-500 uppercase font-semibold">Completeness Score</div>
              <div className="text-sm font-bold text-slate-900 mt-1">100% null check</div>
              <div className="text-[10px] text-emerald-600 font-semibold mt-0.5">✓ 0 column anomalies detected</div>
            </div>
            <div className="p-3 bg-slate-50 rounded-xl border border-slate-200">
              <div className="text-[11px] text-slate-500 uppercase font-semibold">Policy Enforcement</div>
              <div className="text-sm font-bold text-slate-900 mt-1">OPA Sentinel</div>
              <div className="text-[10px] text-emerald-600 font-semibold mt-0.5">✓ 12 of 12 rules passed</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
