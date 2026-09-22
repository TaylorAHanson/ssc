import { useState, useRef, useEffect } from 'react';
import {
  TrendingUp,
  Database,
  LayoutDashboard,
  Box,
  ChevronDown,
  ChevronRight,
  ArrowLeft,
  ShieldCheck,
  UserCheck,
  ExternalLink,
  Layers,
  X,
} from 'lucide-react';
import type { DomainHierarchy, DataAsset, SubdomainMeta } from '../../services/api';
import { MetricViewCard } from './MetricViewCard';
import { DiscoverSearch } from './DiscoverSearch';
import { InlineMetricViewDetail } from './InlineMetricViewDetail';
import { assetWorkspaceUrl } from '../../lib/databricksLinks';

interface DomainFullViewProps {
  domain: string;
  domainsHierarchy: DomainHierarchy[];
  onSelectDomain: (domain: string) => void;
  subdomain: string | null;
  onSelectSubdomain: (subdomain: string | null) => void;
  onBackToStart: () => void;
  metricViews: DataAsset[];
  tables: DataAsset[];
  dashboards?: Array<{
    id: string;
    name: string;
    type: string;
    description: string;
    views: number;
    updated_at: string;
  }>;
  apps?: Array<{
    id: string;
    name: string;
    type: string;
    description: string;
    views: number;
    updated_at: string;
  }>;
  consumingSolutions?: Array<{
    id: string;
    name: string;
    type: string;
    description: string;
    views: number;
    updated_at: string;
  }>;
  showCertifiedOnly: boolean;
  onToggleCertifiedOnly: () => void;
  accessibleAvailable?: boolean;
  showAccessibleOnly?: boolean;
  onToggleAccessibleOnly?: () => void;
  selectedMetricView: DataAsset | null;
  onSelectMetricView: (mv: DataAsset | null) => void;
  onSelectTable: (tbl: DataAsset) => void;
  onRequestAccess?: (asset: DataAsset) => void;
  workspaceUrl: string;
  searchTerm: string;
  onSearchChange: (value: string) => void;
  onSubmitAgentQuery?: (query: string) => void;
}

export function DomainFullView({
  domain,
  domainsHierarchy,
  onSelectDomain,
  subdomain,
  onSelectSubdomain,
  onBackToStart,
  metricViews,
  tables,
  dashboards,
  apps,
  consumingSolutions = [],
  showCertifiedOnly,
  onToggleCertifiedOnly,
  accessibleAvailable = false,
  showAccessibleOnly = false,
  onToggleAccessibleOnly,
  selectedMetricView,
  onSelectMetricView,
  onSelectTable,
  onRequestAccess,
  workspaceUrl,
  searchTerm,
  onSearchChange,
  onSubmitAgentQuery,
}: DomainFullViewProps) {
  const currentDomainData = domainsHierarchy.find((d) => d.domain === domain) || domainsHierarchy[0];
  const currentSubdomainMeta = currentDomainData?.subdomains.find((sd) => sd.name === subdomain);

  // Derive split dashboards and apps lists
  const dashboardsList = dashboards ?? consumingSolutions.filter((s) => s.type !== 'app');
  const appsList = apps ?? consumingSolutions.filter((s) => s.type === 'app');

  // Content tab: default to metric views if there are any, else tables
  const [selectedTab, setSelectedTab] = useState<'metrics' | 'tables' | 'dashboards' | 'apps' | null>(null);
  const activeTab = selectedTab ?? (metricViews.length > 0 ? 'metrics' : tables.length > 0 ? 'tables' : 'dashboards');
  const setActiveTab = (tab: 'metrics' | 'tables' | 'dashboards' | 'apps') => setSelectedTab(tab);

  // Automatically switch to metrics tab if a metric view is selected
  useEffect(() => {
    if (selectedMetricView) {
      setSelectedTab('metrics');
    }
  }, [selectedMetricView]);

  // Dropdown states for compact header navigation
  const [domainDropdownOpen, setDomainDropdownOpen] = useState(false);
  const [subdomainDropdownOpen, setSubdomainDropdownOpen] = useState(false);
  const domainRef = useRef<HTMLDivElement | null>(null);
  const subdomainRef = useRef<HTMLDivElement | null>(null);

  // Click outside to close dropdowns
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (domainRef.current && !domainRef.current.contains(e.target as Node)) {
        setDomainDropdownOpen(false);
      }
      if (subdomainRef.current && !subdomainRef.current.contains(e.target as Node)) {
        setSubdomainDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* STICKY MASKING WRAPPER: Whites out / covers scrolling content so nothing peeks above */}
      <div className="sticky -top-6 -mt-3 pt-4 pb-2 z-20 bg-surface">
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            {/* Back to Start Screen */}
            <button
              type="button"
              onClick={() => {
                onSelectMetricView(null);
                onBackToStart();
              }}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-slate-100 hover:bg-slate-200/80 text-slate-700 hover:text-slate-900 text-xs font-semibold transition-all cursor-pointer"
              title="Return to Domain start screen"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              <span>All Domains</span>
            </button>

            <span className="h-4 w-px bg-slate-200 hidden sm:block" />

            {/* Domain Dropdown Switcher */}
            <div className="relative" ref={domainRef}>
              <button
                type="button"
                onClick={() => {
                  setDomainDropdownOpen(!domainDropdownOpen);
                  setSubdomainDropdownOpen(false);
                }}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-slate-50 hover:bg-slate-100 border border-slate-200/90 text-slate-800 text-xs font-bold transition-all cursor-pointer"
              >
                <Layers className="w-3.5 h-3.5 text-slate-500" />
                <span>{domain}</span>
                <ChevronDown className={`w-3.5 h-3.5 text-slate-400 transition-transform ${domainDropdownOpen ? 'rotate-180' : ''}`} />
              </button>

              {domainDropdownOpen && (
                <div className="absolute left-0 top-full mt-1.5 w-60 bg-white rounded-xl shadow-xl border border-slate-200 py-1.5 z-50 animate-in fade-in slide-in-from-top-1 text-left">
                  <div className="px-3 py-1 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                    Switch Domain
                  </div>
                  {domainsHierarchy.map((dom) => (
                    <button
                      key={dom.domain}
                      type="button"
                      onClick={() => {
                        onSelectDomain(dom.domain);
                        onSelectSubdomain(null);
                        onSelectMetricView(null);
                        setDomainDropdownOpen(false);
                      }}
                      className={`w-full text-left px-3 py-2 text-xs font-medium flex items-center justify-between hover:bg-slate-50 transition-colors ${
                        dom.domain === domain ? 'bg-primary/10 text-primary font-bold' : 'text-slate-700'
                      }`}
                    >
                      <span>{dom.domain}</span>
                      <span className="text-[10px] text-slate-400 font-semibold">{dom.metric_view_count}m</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <ChevronRight className="w-3.5 h-3.5 text-slate-400 hidden sm:block" />

            {/* Subdomain Dropdown Switcher */}
            <div className="relative" ref={subdomainRef}>
              <button
                type="button"
                onClick={() => {
                  setSubdomainDropdownOpen(!subdomainDropdownOpen);
                  setDomainDropdownOpen(false);
                }}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl border text-xs font-bold transition-all cursor-pointer ${
                  subdomain
                    ? 'bg-primary/10 text-primary border-primary/30'
                    : 'bg-slate-50 text-slate-700 border-slate-200/90 hover:bg-slate-100'
                }`}
              >
                <span>{subdomain ? `Subdomain: ${subdomain}` : 'All Subdomains'}</span>
                <ChevronDown className={`w-3.5 h-3.5 text-slate-400 transition-transform ${subdomainDropdownOpen ? 'rotate-180' : ''}`} />
              </button>

              {subdomainDropdownOpen && (
                <div className="absolute left-0 top-full mt-1.5 w-64 bg-white rounded-xl shadow-xl border border-slate-200 py-1.5 z-50 animate-in fade-in slide-in-from-top-1 text-left">
                  <div className="px-3 py-1 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                    Filter by Subdomain
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      onSelectSubdomain(null);
                      onSelectMetricView(null);
                      setSubdomainDropdownOpen(false);
                    }}
                    className={`w-full text-left px-3 py-2 text-xs font-medium flex items-center justify-between hover:bg-slate-50 transition-colors ${
                      !subdomain ? 'bg-primary/10 text-primary font-bold' : 'text-slate-700'
                    }`}
                  >
                    <span>All Subdomains</span>
                    <span className="text-[10px] text-slate-400">{currentDomainData?.metric_view_count} metrics</span>
                  </button>
                  <div className="h-px bg-slate-100 my-1" />
                  {currentDomainData?.subdomains.map((sd: SubdomainMeta) => (
                    <button
                      key={sd.name}
                      type="button"
                      onClick={() => {
                        onSelectSubdomain(sd.name);
                        onSelectMetricView(null);
                        setSubdomainDropdownOpen(false);
                      }}
                      className={`w-full text-left px-3 py-2 text-xs font-medium flex items-center justify-between hover:bg-slate-50 transition-colors ${
                        sd.name === subdomain ? 'bg-primary/10 text-primary font-bold' : 'text-slate-700'
                      }`}
                    >
                      <span>{sd.name}</span>
                      <span className="text-[10px] text-slate-400 font-semibold">{sd.metric_views_count}m • {sd.tables_count}t</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {subdomain && (
              <button
                type="button"
                onClick={() => {
                  onSelectSubdomain(null);
                  onSelectMetricView(null);
                }}
                className="p-1 hover:bg-slate-100 rounded-lg text-slate-400 hover:text-slate-600 transition-colors cursor-pointer"
                title="Show all subdomains"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}

            {/* Selected Metric View Breadcrumb Chip */}
            {selectedMetricView && (
              <>
                <ChevronRight className="w-3.5 h-3.5 text-slate-400 hidden sm:block" />
                <div className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-primary/10 border border-primary/20 text-primary text-xs font-bold shadow-2xs">
                  <TrendingUp className="w-3.5 h-3.5 text-primary" />
                  <span className="max-w-[170px] truncate capitalize">
                    {selectedMetricView.table_name
                      .replace(/^(metric_|sem_)/i, '')
                      .replace(/_metric_view$/i, '')
                      .replace(/_/g, ' ')}
                  </span>
                  <button
                    type="button"
                    onClick={() => onSelectMetricView(null)}
                    className="p-0.5 rounded-md hover:bg-primary/20 text-primary transition-colors cursor-pointer"
                    title="Close metric view details"
                  >
                    <X className="w-3 h-3 stroke-[2.5]" />
                  </button>
                </div>
              </>
            )}
          </div>

        {/* Right side: Accessible toggle, Certified toggle & Collapsed Search */}
        <div className="flex items-center gap-2">
          {accessibleAvailable && onToggleAccessibleOnly && (
            <button
              type="button"
              onClick={onToggleAccessibleOnly}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all cursor-pointer ${
                showAccessibleOnly
                  ? 'bg-blue-50 text-blue-800 border-blue-300 shadow-2xs font-bold'
                  : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'
              }`}
              title="Filter to data assets you have permission to query in Unity Catalog"
            >
              <UserCheck className="w-3.5 h-3.5 text-blue-600" />
              <span className="hidden sm:inline">Accessible to me</span>
            </button>
          )}

          <button
            type="button"
            onClick={onToggleCertifiedOnly}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all cursor-pointer ${
              showCertifiedOnly
                ? 'bg-emerald-50 text-emerald-800 border-emerald-300 shadow-2xs font-bold'
                : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
            <span className="hidden sm:inline">Certified Only</span>
          </button>

          <DiscoverSearch
            value={searchTerm}
            onChange={onSearchChange}
            onSubmitAgentQuery={onSubmitAgentQuery}
          />
        </div>
      </div>
    </div>

      {/* SUBDOMAIN FOCUSED SUMMARY (When a subdomain is chosen) */}
      {currentSubdomainMeta && (
        <div className="bg-gradient-to-r from-primary/5 via-white to-slate-50 border border-primary/20 rounded-2xl p-5 shadow-2xs">
          <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
            <div className="space-y-1.5 flex-1">
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-md bg-primary/10 text-primary border border-primary/20">
                  Subdomain Focus
                </span>
                <span className="text-xs text-slate-400">•</span>
                <span className="text-xs text-slate-500 font-medium">in {domain}</span>
              </div>

              <h2 className="text-2xl font-black text-slate-900 tracking-tight">
                {currentSubdomainMeta.name}
              </h2>
              <p className="text-xs text-slate-600 max-w-3xl leading-relaxed">
                {currentSubdomainMeta.description}
              </p>

              {/* Subdomain Exposed KPIs Summary */}
              {currentSubdomainMeta.kpis && currentSubdomainMeta.kpis.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 pt-2">
                  <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mr-1">
                    Exposed KPIs:
                  </span>
                  {currentSubdomainMeta.kpis.map((kpi, idx) => (
                    <div
                      key={idx}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white border border-primary/20 shadow-2xs text-xs"
                      title={kpi.formula ? `Formula: ${kpi.formula}` : kpi.description}
                    >
                      <span className="text-slate-500 font-medium">{kpi.name}:</span>
                      <span className="font-bold text-slate-900">{kpi.value}</span>
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

            {/* Quick stats on the right: 4 columns for Metric Views, Tables, Dashboards, and Apps */}
            <div className="flex flex-wrap items-center gap-1 sm:gap-2 bg-white border border-slate-200 rounded-2xl p-2 shadow-2xs self-start shrink-0">
              <button
                type="button"
                onClick={() => setActiveTab('metrics')}
                className={`text-center px-3 py-1.5 rounded-xl transition-all cursor-pointer ${
                  activeTab === 'metrics'
                    ? 'bg-primary/10 text-primary font-bold ring-1 ring-primary/30'
                    : 'hover:bg-slate-50 text-slate-700'
                }`}
                title="View Governed Metric Views"
              >
                <div className="text-lg font-bold text-primary">{metricViews.length}</div>
                <div className="text-[10px] text-slate-500 font-medium uppercase tracking-wider">Metric Views</div>
              </button>

              <div className="h-8 w-px bg-slate-200 hidden sm:block" />

              <button
                type="button"
                onClick={() => setActiveTab('tables')}
                className={`text-center px-3 py-1.5 rounded-xl transition-all cursor-pointer ${
                  activeTab === 'tables'
                    ? 'bg-indigo-50 text-indigo-900 font-bold ring-1 ring-indigo-300'
                    : 'hover:bg-slate-50 text-slate-700'
                }`}
                title="View Lakehouse Tables & Views"
              >
                <div className="text-lg font-bold text-indigo-700">{tables.length}</div>
                <div className="text-[10px] text-slate-500 font-medium uppercase tracking-wider">Tables & Views</div>
              </button>

              <div className="h-8 w-px bg-slate-200 hidden sm:block" />

              <button
                type="button"
                onClick={() => setActiveTab('dashboards')}
                className={`text-center px-3 py-1.5 rounded-xl transition-all cursor-pointer ${
                  activeTab === 'dashboards'
                    ? 'bg-amber-50 text-amber-900 font-bold ring-1 ring-amber-300'
                    : 'hover:bg-slate-50 text-slate-700'
                }`}
                title="View Lakeview Dashboards"
              >
                <div className="text-lg font-bold text-amber-600">{dashboardsList.length}</div>
                <div className="text-[10px] text-slate-500 font-medium uppercase tracking-wider">Dashboards</div>
              </button>

              <div className="h-8 w-px bg-slate-200 hidden sm:block" />

              <button
                type="button"
                onClick={() => setActiveTab('apps')}
                className={`text-center px-3 py-1.5 rounded-xl transition-all cursor-pointer ${
                  activeTab === 'apps'
                    ? 'bg-purple-50 text-purple-900 font-bold ring-1 ring-purple-300'
                    : 'hover:bg-slate-50 text-slate-700'
                }`}
                title="View Databricks Apps"
              >
                <div className="text-lg font-bold text-purple-600">{appsList.length}</div>
                <div className="text-[10px] text-slate-500 font-medium uppercase tracking-wider">Apps</div>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* SIMPLIFIED CONTENT: Metric Views | Tables & Views | Dashboards | Apps */}
      <div className="space-y-4">
        {/* Category Tabs: Split Dashboards and Apps */}
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 pb-2.5">
          <button
            type="button"
            onClick={() => setActiveTab('metrics')}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold transition-all cursor-pointer ${
              activeTab === 'metrics'
                ? 'bg-primary text-white shadow-xs'
                : 'bg-white text-slate-600 hover:text-slate-900 border border-slate-200 hover:bg-slate-50'
            }`}
          >
            <TrendingUp className="w-3.5 h-3.5" />
            <span>Governed Metric Views ({metricViews.length})</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('tables')}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold transition-all cursor-pointer ${
              activeTab === 'tables'
                ? 'bg-indigo-700 text-white shadow-xs'
                : 'bg-white text-slate-600 hover:text-slate-900 border border-slate-200 hover:bg-slate-50'
            }`}
          >
            <Database className="w-3.5 h-3.5" />
            <span>Lakehouse Tables & Views ({tables.length})</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('dashboards')}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold transition-all cursor-pointer ${
              activeTab === 'dashboards'
                ? 'bg-amber-600 text-white shadow-xs'
                : 'bg-white text-slate-600 hover:text-slate-900 border border-slate-200 hover:bg-slate-50'
            }`}
          >
            <LayoutDashboard className="w-3.5 h-3.5" />
            <span>Lakeview Dashboards ({dashboardsList.length})</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('apps')}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold transition-all cursor-pointer ${
              activeTab === 'apps'
                ? 'bg-purple-700 text-white shadow-xs'
                : 'bg-white text-slate-600 hover:text-slate-900 border border-slate-200 hover:bg-slate-50'
            }`}
          >
            <Box className="w-3.5 h-3.5" />
            <span>Databricks Apps ({appsList.length})</span>
          </button>
        </div>

        {/* TAB 1: METRIC VIEWS */}
        {activeTab === 'metrics' && (
          <div className="space-y-6">
            {/* Inline Deep-Dive Detail Section: Placed at the top when selected */}
            {selectedMetricView && (
              <InlineMetricViewDetail
                asset={selectedMetricView}
                onClose={() => onSelectMetricView(null)}
                onRequestAccess={onRequestAccess}
                onSelectTable={(tableName) => {
                  const found = tables.find((t) => t.table_name === tableName || t.id === tableName);
                  if (found) onSelectTable(found);
                }}
                workspaceUrl={workspaceUrl}
              />
            )}

            {metricViews.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {metricViews.map((mv) => (
                  <MetricViewCard
                    key={mv.id}
                    asset={mv}
                    isSelected={selectedMetricView?.id === mv.id}
                    onSelect={(asset) => {
                      const next = selectedMetricView?.id === asset.id ? null : asset;
                      onSelectMetricView(next);
                      if (next) {
                        window.scrollTo({ top: 0, behavior: 'smooth' });
                        document.querySelector('main')?.scrollTo({ top: 0, behavior: 'smooth' });
                      }
                    }}
                    onRequestAccess={onRequestAccess}
                  />
                ))}
              </div>
            ) : (
              <div className="bg-slate-50 rounded-2xl border border-slate-200 p-8 text-center">
                <Database className="w-8 h-8 text-indigo-500 mx-auto mb-2" />
                <h4 className="text-sm font-bold text-slate-800">
                  No metric views cataloged for this selection
                </h4>
                <p className="text-xs text-slate-500 mt-1 max-w-sm mx-auto">
                  {subdomain
                    ? `"${subdomain}" models its data assets directly as physical Lakehouse tables and views.`
                    : `No metric views match the current filters in ${domain}.`}
                </p>
                {tables.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setActiveTab('tables')}
                    className="mt-3 px-3.5 py-1.5 text-xs font-semibold text-indigo-700 bg-white border border-indigo-200 rounded-lg hover:bg-indigo-50 shadow-xs cursor-pointer"
                  >
                    View {tables.length} Lakehouse Tables & Views &rarr;
                  </button>
                )}
              </div>
            )}
          </div>
        )}

        {/* TAB 2: LAKEHOUSE TABLES & VIEWS */}
        {activeTab === 'tables' && (
          <div className="bg-white rounded-xl border border-slate-200 shadow-xs overflow-hidden">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
                  <th className="py-2.5 px-4">Asset Name</th>
                  <th className="py-2.5 px-4">Type</th>
                  <th className="py-2.5 px-4">Location</th>
                  <th className="py-2.5 px-4">Description</th>
                  <th className="py-2.5 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {tables.slice(0, 30).map((tbl) => (
                  <tr key={tbl.id} className="hover:bg-slate-50/70 transition-colors">
                    <td className="py-3 px-4 font-semibold text-slate-900">
                      <div className="flex items-center gap-2">
                        <span
                          onClick={() => onSelectTable(tbl)}
                          className="hover:text-primary cursor-pointer"
                        >
                          {tbl.table_name}
                        </span>
                        {tbl.certified && (
                          <span title="Certified">
                            <ShieldCheck className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      <span className="px-2 py-0.5 rounded-md bg-slate-100 text-slate-700 font-medium text-[11px]">
                        {tbl.type}
                      </span>
                    </td>
                    <td className="py-3 px-4 font-mono text-[11px] text-slate-500 truncate max-w-xs">
                      {tbl.catalog}.{tbl.schema_name}
                    </td>
                    <td className="py-3 px-4 text-slate-600 max-w-md line-clamp-2">
                      {tbl.description || 'Governed Lakehouse data asset in Unity Catalog.'}
                    </td>
                    <td className="py-3 px-4 text-right space-x-2">
                      <button
                        type="button"
                        onClick={() => onSelectTable(tbl)}
                        className="text-xs font-semibold text-primary hover:text-primary/80 cursor-pointer"
                      >
                        Details
                      </button>
                      {(() => {
                        const href = assetWorkspaceUrl(workspaceUrl, tbl);
                        if (!href) return null;
                        return (
                          <a
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-slate-400 hover:text-slate-600 inline-flex items-center gap-0.5 text-xs"
                            title="Open in Unity Catalog"
                          >
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        );
                      })()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {tables.length > 30 && (
              <div className="p-3 bg-slate-50 border-t border-slate-100 text-center text-xs text-slate-500">
                Showing first 30 of {tables.length} tables & views in {subdomain || domain}.
              </div>
            )}
            {tables.length === 0 && (
              <div className="p-8 text-center text-xs text-slate-500">
                No tables or views found for this selection.
              </div>
            )}
          </div>
        )}

        {/* TAB 3: LAKEVIEW DASHBOARDS */}
        {activeTab === 'dashboards' && (
          <div className="space-y-4">
            {dashboardsList.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {dashboardsList.map((dash) => (
                  <div
                    key={dash.id}
                    className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm hover:border-amber-300 transition-all flex flex-col justify-between"
                  >
                    <div>
                      <div className="flex items-center justify-between gap-2 mb-2">
                        <span className="text-[11px] font-semibold uppercase px-2 py-0.5 rounded-md border bg-amber-50 text-amber-700 border-amber-200 inline-flex items-center gap-1">
                          <LayoutDashboard className="w-3 h-3" /> Dashboard
                        </span>
                        <span className="text-[11px] text-slate-400">{dash.updated_at}</span>
                      </div>
                      <h4 className="font-bold text-slate-900 text-sm mb-1">{dash.name}</h4>
                      <p className="text-xs text-slate-600 line-clamp-2 leading-relaxed">
                        {dash.description}
                      </p>
                    </div>
                    <div className="pt-3 border-t border-slate-100 mt-3 flex items-center justify-between">
                      <span className="text-[11px] text-slate-500">{dash.views} views</span>
                      <button
                        type="button"
                        onClick={() => alert(`Opening Dashboard: ${dash.name}`)}
                        className="inline-flex items-center gap-1 text-xs font-semibold text-amber-700 hover:text-amber-800 cursor-pointer"
                      >
                        <span>Open Dashboard</span>
                        <ExternalLink className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-8 text-center text-xs text-slate-500 bg-slate-50 rounded-xl border border-slate-200">
                No Lakeview dashboards linked to this subdomain.
              </div>
            )}
          </div>
        )}

        {/* TAB 4: DATABRICKS APPS */}
        {activeTab === 'apps' && (
          <div className="space-y-4">
            {appsList.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {appsList.map((app) => (
                  <div
                    key={app.id}
                    className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm hover:border-purple-300 transition-all flex flex-col justify-between"
                  >
                    <div>
                      <div className="flex items-center justify-between gap-2 mb-2">
                        <span className="text-[11px] font-semibold uppercase px-2 py-0.5 rounded-md border bg-purple-50 text-purple-700 border-purple-200 inline-flex items-center gap-1">
                          <Box className="w-3 h-3" /> Databricks App
                        </span>
                        <span className="text-[11px] text-slate-400">{app.updated_at}</span>
                      </div>
                      <h4 className="font-bold text-slate-900 text-sm mb-1">{app.name}</h4>
                      <p className="text-xs text-slate-600 line-clamp-2 leading-relaxed">
                        {app.description}
                      </p>
                    </div>
                    <div className="pt-3 border-t border-slate-100 mt-3 flex items-center justify-between">
                      <span className="text-[11px] text-slate-500">{app.views} views</span>
                      <button
                        type="button"
                        onClick={() => alert(`Launching Databricks App: ${app.name}`)}
                        className="inline-flex items-center gap-1 text-xs font-semibold text-purple-700 hover:text-purple-800 cursor-pointer"
                      >
                        <span>Launch App</span>
                        <ExternalLink className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-8 text-center text-xs text-slate-500 bg-slate-50 rounded-xl border border-slate-200">
                No Databricks apps registered for this subdomain.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
