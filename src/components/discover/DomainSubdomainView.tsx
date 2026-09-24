import {
  TrendingUp,
  Database,
  Layers,
  ChevronRight,
  Sparkles,
  LayoutDashboard,
  X,
} from 'lucide-react';
import type { DomainHierarchy, SubdomainMeta } from '../../services/api';

interface DomainSubdomainViewProps {
  domains: DomainHierarchy[];
  selectedDomain: string;
  onSelectDomain: (domain: string) => void;
  selectedSubdomain: string | null;
  onSelectSubdomain: (subdomain: string | null) => void;
  onExploreMetricView?: (metricViewName: string) => void;
}

export function DomainSubdomainView({
  domains,
  selectedDomain,
  onSelectDomain,
  selectedSubdomain,
  onSelectSubdomain,
  onExploreMetricView,
}: DomainSubdomainViewProps) {
  const currentDomainData = domains.find((d) => d.domain === selectedDomain) || domains[0];
  const selectedSubdomainMeta = currentDomainData?.subdomains.find(
    (sd) => sd.name === selectedSubdomain
  );

  return (
    <div className="space-y-5">
      {/* Domain Switcher Pills Bar */}
      <div className="flex flex-wrap items-center gap-2 pb-1 border-b border-slate-200">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider mr-2 flex items-center gap-1.5">
          <Layers className="w-3.5 h-3.5 text-slate-400" /> Domain:
        </span>
        {domains.map((dom) => {
          const isSelected = dom.domain === currentDomainData?.domain;
          return (
            <button
              key={dom.domain}
              type="button"
              onClick={() => {
                onSelectDomain(dom.domain);
                onSelectSubdomain(null); // Reset subdomain filter on domain switch
              }}
              className={`inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-medium transition-all cursor-pointer ${
                isSelected
                  ? 'bg-slate-900 text-white shadow-sm ring-2 ring-slate-900/20'
                  : 'bg-white text-slate-700 hover:bg-slate-100 border border-slate-200'
              }`}
            >
              <span className="font-semibold">{dom.domain}</span>
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded-full font-semibold ${
                  isSelected ? 'bg-slate-700 text-slate-200' : 'bg-slate-100 text-slate-600'
                }`}
              >
                {dom.metric_view_count > 0 ? `${dom.metric_view_count} metrics` : `${dom.table_count} tables`}
              </span>
            </button>
          );
        })}
      </div>

      {currentDomainData && (
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-xs p-5 sm:p-6 text-slate-900 transition-all">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
              <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-md bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs font-semibold tracking-wide uppercase mb-1.5">
                <Sparkles className="w-3.5 h-3.5 text-emerald-600" /> Business Domain
              </div>
              <h2 className="text-2xl font-bold tracking-tight text-slate-900 flex items-center gap-3">
                {currentDomainData.domain}
              </h2>
              <p className="mt-1 text-sm text-slate-600 max-w-2xl leading-relaxed">
                {currentDomainData.description}
              </p>
            </div>

            {/* Headline Stats Chips */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 bg-slate-50 border border-slate-200/80 rounded-xl p-3">
              <div className="text-center px-2">
                <div className="text-xl font-bold text-slate-900">{currentDomainData.subdomain_count}</div>
                <div className="text-[11px] text-slate-500 uppercase font-medium">Subdomains</div>
              </div>
              <div className="text-center px-2 border-l border-slate-200">
                <div className="text-xl font-bold text-emerald-700">{currentDomainData.metric_view_count}</div>
                <div className="text-[11px] text-slate-500 uppercase font-medium">Metric Views</div>
              </div>
              <div className="text-center px-2 border-l border-slate-200">
                <div className="text-xl font-bold text-indigo-700">{currentDomainData.table_count}</div>
                <div className="text-[11px] text-slate-500 uppercase font-medium">Tables & Views</div>
              </div>
              <div className="text-center px-2 border-l border-slate-200">
                <div className="text-xl font-bold text-amber-700">{currentDomainData.dashboard_count}</div>
                <div className="text-[11px] text-slate-500 uppercase font-medium">Dashboards</div>
              </div>
            </div>
          </div>

          {/* Interactive Nesting Breadcrumb: Domain > Subdomain (with clear X) > [Metric Views | Tables & Views] > Dashboards */}
          <div className="mt-4 pt-3.5 border-t border-slate-100 flex flex-wrap items-center gap-2 text-xs text-slate-600">
            <span className="font-semibold text-slate-500 uppercase tracking-wider text-[11px]">How it nests:</span>
            
            {/* Domain chip */}
            <button
              type="button"
              onClick={() => onSelectSubdomain(null)}
              className="inline-flex items-center gap-1.5 bg-slate-100 hover:bg-slate-200/80 border border-slate-200 px-2.5 py-1 rounded-lg text-slate-800 font-semibold transition-colors cursor-pointer"
              title="Domain level (clears subdomain filter)"
            >
              <Layers className="w-3.5 h-3.5 text-slate-500" />
              <span>{currentDomainData.domain}</span>
            </button>

            <ChevronRight className="w-3.5 h-3.5 text-slate-400" />

            {/* Subdomain chip with prominent clear (X) */}
            {selectedSubdomain ? (
              <div className="inline-flex items-center gap-1.5 bg-emerald-50 border border-emerald-300 px-2.5 py-1 rounded-lg text-emerald-800 font-bold shadow-2xs">
                <span>{selectedSubdomain}</span>
                <button
                  type="button"
                  onClick={() => onSelectSubdomain(null)}
                  className="p-0.5 hover:bg-emerald-200/80 rounded text-emerald-700 hover:text-emerald-950 transition-colors ml-0.5 cursor-pointer"
                  title={`Clear ${selectedSubdomain} (expand all subdomains)`}
                >
                  <X className="w-3.5 h-3.5 stroke-[2.5]" />
                </button>
              </div>
            ) : (
              <div className="inline-flex items-center gap-1.5 bg-slate-100/70 border border-slate-200/60 px-2.5 py-1 rounded-lg text-slate-600 font-medium">
                <span>All Subdomains</span>
              </div>
            )}

            <ChevronRight className="w-3.5 h-3.5 text-slate-400" />

            {/* Third level: Shows that subdomains nest BOTH Metric Views and top-level Tables & Views */}
            <div className="inline-flex items-center gap-2 bg-slate-50 border border-slate-200/80 px-2.5 py-1 rounded-lg text-slate-700 font-medium">
              <span className="inline-flex items-center gap-1 text-emerald-700 font-semibold">
                <TrendingUp className="w-3 h-3" />
                Metric Views
              </span>
              <span className="text-slate-300">|</span>
              <span className="inline-flex items-center gap-1 text-indigo-700 font-semibold">
                <Database className="w-3 h-3" />
                Tables & Views
              </span>
            </div>

            <ChevronRight className="w-3.5 h-3.5 text-slate-400" />

            {/* Fourth level: Consuming dashboards and apps */}
            <div className="inline-flex items-center gap-1.5 bg-slate-50 border border-slate-200/80 text-amber-700 px-2.5 py-1 rounded-lg font-medium">
              <LayoutDashboard className="w-3 h-3 text-amber-600" />
              <span>Dashboards & Apps</span>
            </div>
          </div>
        </div>
      )}

      {/* Subdomains Section */}
      <div>
        {selectedSubdomain ? (
          /* COLLAPSED VIEW: When a subdomain is clicked, other subdomain cards collapse! */
          <div className="space-y-4 animate-in fade-in duration-200">
            {/* Quick horizontal switch row for all subdomains in this domain */}
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs text-slate-500 font-medium mr-1">Switch Subdomain:</span>
              {currentDomainData?.subdomains.map((sd: SubdomainMeta) => {
                const isSelected = sd.name === selectedSubdomain;
                return (
                  <button
                    key={sd.name}
                    type="button"
                    onClick={() => onSelectSubdomain(isSelected ? null : sd.name)}
                    className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-medium transition-all cursor-pointer ${
                      isSelected
                        ? 'bg-emerald-600 text-white font-bold shadow-2xs'
                        : 'bg-white border border-slate-200 text-slate-700 hover:bg-slate-100 hover:text-slate-900'
                    }`}
                  >
                    <span>{sd.name}</span>
                    <span
                      className={`text-[10px] px-1.5 py-0.2 rounded-full font-bold ${
                        isSelected ? 'bg-emerald-700 text-emerald-100' : 'bg-slate-100 text-slate-600'
                      }`}
                    >
                      {sd.metric_views_count > 0 ? `${sd.metric_views_count}m` : `${sd.tables_count}t`}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* Focused Active Subdomain Card */}
            {selectedSubdomainMeta && (
              <div className="bg-gradient-to-r from-emerald-50/60 via-white to-slate-50 border border-emerald-200 rounded-2xl p-5 shadow-xs">
                <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
                  <div className="space-y-1.5 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-md bg-emerald-100 text-emerald-800 border border-emerald-200">
                        Active Subdomain
                      </span>
                      <span className="text-xs text-slate-400">•</span>
                      <span className="text-xs text-slate-500 font-medium">in {currentDomainData?.domain}</span>
                    </div>

                    <h3 className="text-xl font-extrabold text-slate-900 tracking-tight">
                      {selectedSubdomainMeta.name}
                    </h3>

                    <p className="text-xs text-slate-600 max-w-2xl leading-relaxed">
                      {selectedSubdomainMeta.description}
                    </p>

                    {/* Subdomain Headline KPIs */}
                    {selectedSubdomainMeta.kpis && selectedSubdomainMeta.kpis.length > 0 && (
                      <div className="flex flex-wrap items-center gap-2 pt-2">
                        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mr-1">
                          Exposed KPIs:
                        </span>
                        {selectedSubdomainMeta.kpis.map((kpi, idx) => (
                          <div
                            key={idx}
                            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white border border-emerald-200 shadow-2xs text-xs"
                          >
                            <span className="text-slate-500 font-medium">{kpi.name}{kpi.value ? ':' : ''}</span>
                            {kpi.value && <span className="font-bold text-slate-900">{kpi.value}</span>}
                            {kpi.trend && (
                              <span
                                className={`text-[10px] font-semibold ${
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
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Stats and Clear button */}
                  <div className="flex items-center gap-3 bg-white border border-slate-200 rounded-xl p-3 shadow-2xs self-start shrink-0">
                    <div className="text-center px-3">
                      <div className="text-lg font-bold text-emerald-700">{selectedSubdomainMeta.metric_views_count}</div>
                      <div className="text-[10px] text-slate-500 font-medium uppercase">Metric Views</div>
                    </div>
                    <div className="h-8 w-px bg-slate-200" />
                    <div className="text-center px-3">
                      <div className="text-lg font-bold text-indigo-700">{selectedSubdomainMeta.tables_count}</div>
                      <div className="text-[10px] text-slate-500 font-medium uppercase">Tables & Views</div>
                    </div>
                    <div className="h-8 w-px bg-slate-200" />
                    <button
                      type="button"
                      onClick={() => onSelectSubdomain(null)}
                      className="px-2.5 py-1 text-xs font-semibold text-slate-600 hover:text-slate-900 hover:bg-slate-50 rounded-lg transition-colors border border-slate-200 flex items-center gap-1 cursor-pointer"
                      title="Clear subdomain filter"
                    >
                      <X className="w-3.5 h-3.5" />
                      <span>All Subdomains</span>
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        ) : (
          /* EXPANDED VIEW: When no subdomain is selected, show all subdomains in the grid */
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <h3 className="text-base font-semibold text-slate-900">
                  Subdomains in {currentDomainData?.domain}
                </h3>
                <span className="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded-full font-medium">
                  {currentDomainData?.subdomains.length || 0}
                </span>
              </div>
              <span className="text-xs text-slate-500">
                Click a subdomain to focus its metric views, tables, and dashboards
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {currentDomainData?.subdomains.map((sd: SubdomainMeta) => (
                <div
                  key={sd.name}
                  onClick={() => onSelectSubdomain(sd.name)}
                  className="relative rounded-xl p-5 border border-slate-200/90 bg-white hover:border-emerald-400 hover:shadow-md transition-all cursor-pointer group flex flex-col justify-between"
                >
                  <div>
                    <div className="flex items-start justify-between gap-2 mb-2">
                      <h4 className="font-semibold text-slate-900 group-hover:text-emerald-700 transition-colors text-base flex items-center gap-2">
                        {sd.name}
                      </h4>
                    </div>

                    <p className="text-xs text-slate-600 mb-4 line-clamp-2 leading-relaxed">
                      {sd.description}
                    </p>

                    {/* Headline KPIs */}
                    {sd.kpis && sd.kpis.length > 0 && (
                      <div className="flex flex-wrap gap-2 mb-3">
                        {sd.kpis.map((kpi, idx) => (
                          <div
                            key={idx}
                            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-slate-50 border border-slate-200 text-xs"
                          >
                            <span className="text-slate-500 font-medium">{kpi.name}{kpi.value ? ':' : ''}</span>
                            {kpi.value && <span className="font-bold text-slate-900">{kpi.value}</span>}
                            {kpi.trend && (
                              <span
                                className={`text-[10px] font-semibold ${
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
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Key Resources: Metric Views if available, or top tables/views */}
                    {sd.metric_views && sd.metric_views.length > 0 ? (
                      <div className="mb-3">
                        <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
                          Metric Views
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {sd.metric_views.slice(0, 3).map((mv) => (
                            <span
                              key={mv}
                              onClick={(e) => {
                                if (onExploreMetricView) {
                                  e.stopPropagation();
                                  onExploreMetricView(mv);
                                }
                              }}
                              className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded bg-emerald-50 text-emerald-800 border border-emerald-200/60 font-mono hover:bg-emerald-100 transition-colors"
                            >
                              <TrendingUp className="w-2.5 h-2.5 text-emerald-600" />
                              {mv.replace(/^(metric_|sem_)/, '').replace(/_metric_view$/, '')}
                            </span>
                          ))}
                          {sd.metric_views.length > 3 && (
                            <span className="text-[10px] text-slate-500 self-center font-medium">
                              +{sd.metric_views.length - 3} more
                            </span>
                          )}
                        </div>
                      </div>
                    ) : sd.schemas && sd.schemas.length > 0 ? (
                      <div className="mb-3">
                        <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
                          Tables
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded bg-indigo-50 text-indigo-800 border border-indigo-200/60 font-mono">
                            <Database className="w-2.5 h-2.5 text-indigo-600" />
                            {sd.schemas[0]} ({sd.tables_count} tables)
                          </span>
                        </div>
                      </div>
                    ) : null}
                  </div>

                  {/* Footer Badges */}
                  <div className="pt-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
                    <div className="flex items-center gap-2 font-medium">
                      {sd.metric_views_count > 0 ? (
                        <>
                          <span className="text-emerald-700 font-semibold inline-flex items-center gap-1">
                            <TrendingUp className="w-3.5 h-3.5" />
                            {sd.metric_views_count} metrics
                          </span>
                          <span className="text-slate-300">•</span>
                        </>
                      ) : null}
                      <span className="inline-flex items-center gap-1 text-slate-600">
                        <Database className="w-3 h-3 text-slate-400" />
                        {sd.tables_count} tables & views
                      </span>
                    </div>

                    <div className="inline-flex items-center gap-1 text-emerald-700 font-semibold group-hover:translate-x-0.5 transition-transform">
                      <span>Explore</span>
                      <ChevronRight className="w-3.5 h-3.5" />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
