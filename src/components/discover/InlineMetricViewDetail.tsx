import { useState, useRef, useEffect } from 'react';
import {
  TrendingUp,
  ShieldCheck,
  ExternalLink,
  LayoutDashboard,
  Database,
  Lock,
  GitBranch,
  Clock,
  User,
  X,
} from 'lucide-react';
import { api } from '../../services/api';
import type { DataAsset, MetricKpi, MetricViewDetail } from '../../services/api';
import { catalogExplorerUrl } from '../../lib/databricksLinks';
import { LineageGraph, type LineageSeedTable } from './LineageGraph';

interface DashboardOrApp {
  id: string;
  name: string;
  type?: string;
  description?: string;
  owner?: string;
  views?: number;
  updated_at?: string;
  url?: string | null;
}

/** Render a raw measure value using the measure's YAML `format`, if any. */
function formatMeasureValue(raw: string | number | null | undefined, format?: MetricKpi['format']): string {
  if (raw === null || raw === undefined || raw === '') return '—';
  const n = typeof raw === 'number' ? raw : Number(raw);
  if (!Number.isFinite(n)) return String(raw);
  const type = format?.type?.toLowerCase();
  if (type === 'percentage') {
    return new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 }).format(n);
  }
  const compact = Math.abs(n) >= 10_000;
  const opts: Intl.NumberFormatOptions = compact
    ? { notation: 'compact', maximumSignificantDigits: 3 }
    : { maximumFractionDigits: Math.abs(n) < 1 ? 4 : 2 };
  if (type === 'currency' && format?.currency_code) {
    try {
      return new Intl.NumberFormat(undefined, { ...opts, style: 'currency', currency: format.currency_code }).format(n);
    } catch {
      // Unknown currency code: fall through to a plain number.
    }
  }
  return new Intl.NumberFormat(undefined, opts).format(n);
}

/** Why the KPI table is empty, in terms the viewer can act on. */
function emptyKpisMessage(detail: MetricViewDetail | null): string {
  if (!detail) return 'Loading measures…';
  switch (detail.reason) {
    case 'permission_denied':
      return "You don't have access to this metric view — request access to see its measures.";
    case 'no_obo':
      return 'Measures are read with your own permissions, which requires signing in through the deployed app.';
    case 'no_warehouse':
      return 'Measures are unavailable: no SQL warehouse is configured.';
    case 'not_found':
      return 'This metric view is no longer in the catalog.';
    case 'error':
      return "Couldn't read this metric view's definition.";
    default:
      return 'This metric view defines no measures.';
  }
}

interface UpstreamTableInfo {
  name: string;
  fqn?: string;
  schema?: string;
  type?: string;
  description?: string;
}

interface InlineMetricViewDetailProps {
  asset: DataAsset;
  onClose: () => void;
  onRequestAccess?: (asset: DataAsset) => void;
  onSelectTable?: (tableName: string) => void;
  workspaceUrl: string;
}

export function InlineMetricViewDetail({
  asset,
  onClose,
  onRequestAccess,
  onSelectTable,
  workspaceUrl,
}: InlineMetricViewDetailProps) {
  const [detailTab, setDetailTab] = useState<'kpis' | 'lineage' | 'dashboards' | 'tables'>('kpis');
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [loaded, setLoaded] = useState<{ assetId: string; result: MetricViewDetail } | null>(null);

  // Read the definition and current values as the user whenever a view is opened,
  // so what's shown reflects their own grants rather than the sync identity's.
  useEffect(() => {
    let cancelled = false;
    api
      .getMetricViewDetail(asset.id)
      .catch(
        (e: Error): MetricViewDetail => ({
          available: false,
          reason: 'error',
          kpis: [],
          upstream_tables: [],
          values: {},
          error: e.message,
        }),
      )
      .then((result) => {
        if (!cancelled) setLoaded({ assetId: asset.id, result });
      });
    return () => {
      cancelled = true;
    };
  }, [asset.id]);
  const detail = loaded?.assetId === asset.id ? loaded.result : null;

  // Start at the top of the panel whenever a different metric view opens.
  useEffect(() => {
    containerRef.current?.scrollIntoView({ block: 'start' });
  }, [asset.id]);

  const catalogUrl = catalogExplorerUrl(
    workspaceUrl,
    asset.catalog,
    asset.schema_name,
    asset.table_name,
  );

  const cleanName = asset.table_name
    .replace(/^(metric_|sem_)/i, '')
    .replace(/_metric_view$/i, '')
    .replace(/_/g, ' ');

  const kpis: MetricKpi[] = detail?.kpis ?? [];
  // All measures in a metric view share its dimensions; show them once.
  const dimensions = Array.from(new Set(kpis.flatMap((k) => k.dimensions ?? [])));

  const downstreamDashboards: DashboardOrApp[] = asset.downstream_dashboards ?? [];

  const upstreamTables: UpstreamTableInfo[] = detail?.upstream_tables ?? [];

  const seedTables: LineageSeedTable[] = [
    {
      fqn: `${asset.catalog}.${asset.schema_name}.${asset.table_name}`,
      displayName: cleanName,
      upstreams: upstreamTables.map((t) => t.fqn || t.name),
      downstreams: downstreamDashboards.map((d) => d.name),
    },
  ];

  return (
    <div
      ref={containerRef}
      className="bg-white rounded-2xl border border-slate-200 shadow-sm p-5 sm:p-6 space-y-6 relative"
    >
      {/* Top Banner & Action Bar */}
      <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4 pb-4 border-b border-slate-100">
        <div className="space-y-1.5 flex-1">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            {asset.certified && (
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" /> Certified
              </span>
            )}

            {asset.domain && (
              <span className="text-[11px] px-2 py-0.5 rounded-md bg-slate-100 text-slate-700 font-medium">
                {asset.domain}
              </span>
            )}

            {asset.subdomain && (
              <span className="text-[11px] px-2 py-0.5 rounded-md bg-primary/10 text-primary font-medium">
                {asset.subdomain}
              </span>
            )}
          </div>

          <h3 className="text-2xl font-black text-slate-900 capitalize tracking-tight flex items-center gap-2">
            <span>{cleanName}</span>
          </h3>

          <p className="font-mono text-xs text-slate-400">
            {asset.catalog}.{asset.schema_name}.{asset.table_name}
          </p>

          {asset.description && (
            <p className="text-xs text-slate-600 max-w-3xl leading-relaxed pt-1">{asset.description}</p>
          )}

          {/* Metadata badges */}
          <div className="flex flex-wrap items-center gap-4 text-xs text-slate-500 pt-2">
            <div className="flex items-center gap-1.5">
              <User className="w-3.5 h-3.5 text-slate-400" />
              <span>Owner: <strong className="text-slate-700">{asset.owner || 'Unknown'}</strong></span>
            </div>
            {asset.sla && (
              <div className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5 text-slate-400" />
                <span>SLA: <strong className="text-slate-700">{asset.sla}</strong></span>
              </div>
            )}
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2 self-start shrink-0">
          {catalogUrl && (
            <a
              href={catalogUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-slate-200 text-xs font-semibold text-slate-700 hover:bg-slate-50 transition-colors shadow-2xs"
              title="Open in Catalog Explorer"
            >
              <ExternalLink className="w-3.5 h-3.5 text-slate-400" />
              <span>Catalog Explorer</span>
            </a>
          )}

          {onRequestAccess && (
            <button
              type="button"
              onClick={() => onRequestAccess(asset)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-primary hover:bg-primary/90 text-white text-xs font-semibold shadow-2xs transition-colors cursor-pointer"
            >
              <Lock className="w-3.5 h-3.5" />
              <span>Request Access</span>
            </button>
          )}

          <button
            type="button"
            onClick={onClose}
            className="inline-flex items-center gap-1 p-1.5 rounded-xl text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors cursor-pointer ml-1"
            title="Close metric view details"
          >
            <X className="w-4 h-4 stroke-[2.5]" />
          </button>
        </div>
      </div>

      {/* Sub-Tabs: KPI Measures | Lineage Flow | Consuming Apps | Upstream Tables */}
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 pb-2">
          <button
            type="button"
            onClick={() => setDetailTab('kpis')}
            className={`inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              detailTab === 'kpis'
                ? 'bg-primary text-white shadow-2xs'
                : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
            }`}
          >
            <TrendingUp className="w-3.5 h-3.5" />
            <span>KPIs{detail ? ` (${kpis.length})` : ''}</span>
          </button>

          <button
            type="button"
            onClick={() => setDetailTab('lineage')}
            className={`inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              detailTab === 'lineage'
                ? 'bg-primary text-white shadow-2xs'
                : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
            }`}
          >
            <GitBranch className="w-3.5 h-3.5" />
            <span>Lineage Flow</span>
          </button>

          <button
            type="button"
            onClick={() => setDetailTab('dashboards')}
            className={`inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              detailTab === 'dashboards'
                ? 'bg-primary text-white shadow-2xs'
                : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
            }`}
          >
            <LayoutDashboard className="w-3.5 h-3.5" />
            <span>Dashboards ({downstreamDashboards.length})</span>
          </button>

          <button
            type="button"
            onClick={() => setDetailTab('tables')}
            className={`inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              detailTab === 'tables'
                ? 'bg-primary text-white shadow-2xs'
                : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
            }`}
          >
            <Database className="w-3.5 h-3.5" />
            <span>Source Tables{detail ? ` (${upstreamTables.length})` : ''}</span>
          </button>
        </div>

        {/* SUBTAB 1: Governed KPI Measures Table */}
        {detailTab === 'kpis' && (
          <div className="space-y-3">
            {dimensions.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                <span className="font-semibold text-slate-500 mr-1">Slice by</span>
                {dimensions.map((dim) => (
                  <span key={dim} className="px-2 py-0.5 bg-slate-100 text-slate-700 rounded-md text-[11px] font-medium">
                    {dim}
                  </span>
                ))}
              </div>
            )}
            <div className="bg-slate-50/50 rounded-xl border border-slate-200 overflow-x-auto shadow-2xs">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="bg-slate-100/70 border-b border-slate-200 text-slate-700 font-semibold">
                    <th className="py-2.5 px-4">KPI</th>
                    <th className="py-2.5 px-4">Current Value</th>
                    <th className="py-2.5 px-4">Formula</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 bg-white">
                  {kpis.length === 0 && (
                    <tr>
                      <td colSpan={3} className="py-6 px-4 text-center text-slate-400 italic">
                        {emptyKpisMessage(detail)}
                      </td>
                    </tr>
                  )}
                  {kpis.map((kpi, idx) => (
                    <tr key={idx} className="hover:bg-slate-50/70 transition-colors">
                      <td className="py-3 px-4 font-semibold text-slate-900">
                        <div className="flex items-center gap-1.5">
                          <span className="w-2 h-2 rounded-full bg-primary shrink-0" />
                          <span>{kpi.name}</span>
                        </div>
                        {kpi.description && (
                          <div className="text-[11px] text-slate-500 font-normal mt-0.5 max-w-xs leading-relaxed">
                            {kpi.description}
                          </div>
                        )}
                      </td>
                      <td className="py-3 px-4 whitespace-nowrap">
                        <span className="font-extrabold text-slate-900 text-sm">
                          {formatMeasureValue(kpi.measure ? detail?.values[kpi.measure] : kpi.value, kpi.format)}
                        </span>
                      </td>
                      <td className="py-3 px-4 font-mono text-[11px] text-primary">
                        <span className="bg-primary/5 border border-primary/20 px-2 py-1 rounded inline-block max-w-sm truncate" title={kpi.formula}>
                          {kpi.formula || '—'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {detail?.error && (detail.reason === 'error' || kpis.length > 0) && (
              <div className="text-[11px] text-amber-700">
                <p>
                  {detail.available ? "Couldn't load current values" : "Couldn't read this metric view"}
                  {detail.error_kind === 'permission_denied' ? ' — you may not have access to this metric view.' : '.'}
                </p>
                <details className="mt-1 text-slate-500">
                  <summary className="cursor-pointer select-none">Show error details</summary>
                  <pre className="mt-1 whitespace-pre-wrap break-all font-mono text-[10px]">{detail.error}</pre>
                </details>
              </div>
            )}
          </div>
        )}

        {/* SUBTAB 2: Interactive Lineage Flow */}
        {detailTab === 'lineage' && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs text-slate-500 pb-1">
              <span>Upstream sources & downstream consumers traced in Unity Catalog</span>
            </div>
            <div className="h-[380px] rounded-xl overflow-hidden border border-slate-200 bg-slate-50 shadow-2xs">
              <LineageGraph
                seedTables={seedTables}
                workspaceUrl={workspaceUrl}
                height="380px"
              />
            </div>
          </div>
        )}

        {/* SUBTAB 3: Consuming Dashboards & Apps */}
        {detailTab === 'dashboards' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {downstreamDashboards.length === 0 && (
              <p className="text-xs text-slate-400 italic">No dashboards or apps are linked to this metric view.</p>
            )}
            {downstreamDashboards.map((dash) => (
              <div
                key={dash.id}
                className="bg-white rounded-xl border border-slate-200 p-4 shadow-2xs hover:border-slate-300 transition-all flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-amber-50 text-amber-800 border border-amber-200">
                      {dash.type || 'Dashboard'}
                    </span>
                    {dash.views != null && (
                      <span className="text-[11px] text-slate-400">{dash.views} views</span>
                    )}
                  </div>
                  <h4 className="text-sm font-bold text-slate-900 mb-1">{dash.name}</h4>
                  <p className="text-xs text-slate-500 leading-relaxed mb-3">
                    {dash.description}
                  </p>
                </div>
                <div className="pt-2 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
                  <span>{dash.owner ? `Owner: ${dash.owner}` : ''}</span>
                  {dash.url ? (
                    <a
                      href={dash.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary font-semibold inline-flex items-center gap-1"
                    >
                      Open <ExternalLink className="w-3 h-3" />
                    </a>
                  ) : (
                    <span className="text-[11px] text-slate-400">No link available</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* SUBTAB 4: Upstream Lakehouse Tables */}
        {detailTab === 'tables' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {upstreamTables.length === 0 && (
              <p className="text-xs text-slate-400 italic">No source tables found in the metric view definition.</p>
            )}
            {upstreamTables.map((tbl, idx) => (
              <div
                key={idx}
                className="bg-white rounded-xl border border-slate-200 p-4 shadow-2xs hover:border-slate-300 transition-all flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-indigo-50 text-indigo-800 border border-indigo-200">
                      {tbl.type || 'Table'}
                    </span>
                    <span className="text-[11px] font-mono text-slate-400">{tbl.schema}</span>
                  </div>
                  <h4 className="text-sm font-bold text-slate-900 mb-1 font-mono">
                    {tbl.name}
                  </h4>
                  <p className="text-xs text-slate-500 leading-relaxed mb-3">
                    {tbl.description}
                  </p>
                </div>
                <div className="pt-2 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
                  <span className="font-mono text-[10px] text-slate-400 truncate max-w-[200px]">
                    {tbl.fqn}
                  </span>
                  {onSelectTable && (
                    <button
                      type="button"
                      onClick={() => onSelectTable(tbl.name)}
                      className="text-primary font-semibold inline-flex items-center gap-1 hover:text-primary/80 cursor-pointer text-xs"
                    >
                      View Schema &rarr;
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
