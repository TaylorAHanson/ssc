import { useState, useRef, useEffect, type ReactNode } from 'react';
import {
  TrendingUp,
  Database,
  LayoutDashboard,
  Box,
  Check,
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
import { AssetCard } from './AssetCard';
import { Pagination } from './Pagination';
import { usePagination } from './usePagination';
import { SidePanel } from './SidePanel';

type ContentKind = 'metrics' | 'tables' | 'dashboards' | 'apps';

const KINDS_STORAGE_KEY = 'discover_content_kinds';
// Cards per page: 4 rows of the 3-column grid.
const PAGE_SIZE = 12;

function loadPickedKinds(): ContentKind[] | null {
  try {
    const raw = JSON.parse(localStorage.getItem(KINDS_STORAGE_KEY) || 'null');
    const valid: ContentKind[] = ['metrics', 'tables', 'dashboards', 'apps'];
    return Array.isArray(raw) && raw.length && raw.every((k) => valid.includes(k)) ? raw : null;
  } catch {
    return null;
  }
}

function plural(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

const CONTENT_KINDS: Array<{
  key: ContentKind;
  label: string;
  Icon: typeof TrendingUp;
  /** Classes when selected / accent when not. */
  on: string;
  off: string;
}> = [
  { key: 'metrics', label: 'Metric Views', Icon: TrendingUp, on: 'bg-primary/10 border-primary text-primary', off: 'text-primary' },
  { key: 'tables', label: 'Tables & Views', Icon: Database, on: 'bg-indigo-50 border-indigo-600 text-indigo-800', off: 'text-indigo-700' },
  { key: 'dashboards', label: 'Dashboards', Icon: LayoutDashboard, on: 'bg-amber-50 border-amber-500 text-amber-800', off: 'text-amber-700' },
  { key: 'apps', label: 'Apps', Icon: Box, on: 'bg-purple-50 border-purple-600 text-purple-800', off: 'text-purple-700' },
];

function SectionHeading({ kind, count }: { kind: ContentKind; count: number }) {
  const { label, Icon, off } = CONTENT_KINDS.find((k) => k.key === kind)!;
  return (
    <h3 className={`flex items-center gap-2 text-sm font-bold ${off}`}>
      <Icon className="w-4 h-4" />
      <span>{label}</span>
      <span className="text-slate-400 font-semibold">({count})</span>
    </h3>
  );
}

const GRID = 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4';

interface LinkItem {
  id: string;
  name: string;
  description: string;
  updated_at?: string;
  url?: string | null;
  thumbnail_url?: string | null;
}

/** ISO / lineage timestamps as a short date; anything unparseable is shown as-is. */
function formatWhen(value?: string) {
  if (!value) return null;
  const d = new Date(value.replace(' ', 'T'));
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

const LINK_TONES = {
  amber: { badge: 'bg-amber-50 text-amber-700 border-amber-200', hover: 'hover:border-amber-300', link: 'text-amber-700 hover:text-amber-800' },
  purple: { badge: 'bg-purple-50 text-purple-700 border-purple-200', hover: 'hover:border-purple-300', link: 'text-purple-700 hover:text-purple-800' },
};

/** Dashboard / app card: same shape as the other cards, but opens the thing itself in Databricks. */
function LinkCard({
  item,
  Icon,
  badge,
  openLabel,
  tone,
}: {
  item: LinkItem;
  Icon: typeof Box;
  badge: string;
  openLabel: string;
  tone: keyof typeof LINK_TONES;
}) {
  const t = LINK_TONES[tone];
  const [thumbFailed, setThumbFailed] = useState(false);
  const when = formatWhen(item.updated_at);
  return (
    <div className={`bg-white rounded-2xl border border-slate-200 p-5 shadow-xs hover:shadow-md ${t.hover} transition-all flex flex-col justify-between`}>
      <div>
        {item.thumbnail_url && !thumbFailed && (
          <img
            src={item.thumbnail_url}
            alt=""
            loading="lazy"
            onError={() => setThumbFailed(true)}
            className="mb-3 w-full aspect-video object-cover rounded-xl border border-slate-100 bg-slate-50"
          />
        )}
        <div className="flex items-center justify-between gap-2 mb-2">
          <span className={`text-[11px] font-semibold uppercase px-2 py-0.5 rounded-md border inline-flex items-center gap-1 ${t.badge}`}>
            <Icon className="w-3 h-3" /> {badge}
          </span>
          {when && <span className="text-[11px] text-slate-400">Updated {when}</span>}
        </div>
        <h4 className="text-base font-bold text-slate-900 mb-1 break-words">{item.name}</h4>
        {item.description ? (
          <p className="text-xs text-slate-600 line-clamp-3 leading-relaxed mb-4">{item.description}</p>
        ) : (
          <p className="mb-4 text-[11px] text-slate-400 italic">No description.</p>
        )}
      </div>
      <div className="pt-3 border-t border-slate-100 flex items-center justify-end">
        {item.url ? (
          <a href={item.url} target="_blank" rel="noopener noreferrer" className={`inline-flex items-center gap-1 text-xs font-semibold ${t.link}`}>
            <span>{openLabel}</span>
            <ExternalLink className="w-3 h-3" />
          </a>
        ) : (
          <span className="text-[11px] text-slate-400">No link available</span>
        )}
      </div>
    </div>
  );
}

function EmptyNote({ children }: { children: ReactNode }) {
  return (
    <p className="text-xs text-slate-500 bg-slate-50 rounded-xl border border-slate-200 px-4 py-3">{children}</p>
  );
}

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
    views?: number;
    updated_at?: string;
    url?: string | null;
  }>;
  apps?: Array<{
    id: string;
    name: string;
    type: string;
    description: string;
    views?: number;
    updated_at?: string;
    url?: string | null;
    thumbnail_url?: string | null;
  }>;
  consumingSolutions?: Array<{
    id: string;
    name: string;
    type: string;
    description: string;
    views?: number;
    updated_at?: string;
    url?: string | null;
  }>;
  showCertifiedOnly: boolean;
  onToggleCertifiedOnly: () => void;
  accessibleAvailable?: boolean;
  showAccessibleOnly?: boolean;
  onToggleAccessibleOnly?: () => void;
  selectedMetricView: DataAsset | null;
  onSelectMetricView: (mv: DataAsset | null) => void;
  onSelectTable: (tbl: DataAsset) => void;
  /** Id of the table currently open in the details panel, to highlight its card. */
  selectedTableId?: string | null;
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
  selectedTableId = null,
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

  // Content kinds shown below the header. Multi-select: until the user picks,
  // show metric views and tables together (whichever have items) so a
  // subdomain with no metric views still lands on something useful.
  const counts: Record<ContentKind, number> = {
    metrics: metricViews.length,
    tables: tables.length,
    dashboards: dashboardsList.length,
    apps: appsList.length,
  };
  const [pickedKinds, setPickedKindsState] = useState<ContentKind[] | null>(loadPickedKinds);
  const setPickedKinds = (update: ContentKind[] | null | ((prev: ContentKind[] | null) => ContentKind[] | null)) =>
    setPickedKindsState((prev) => {
      const next = typeof update === 'function' ? update(prev) : update;
      try {
        if (next) localStorage.setItem(KINDS_STORAGE_KEY, JSON.stringify(next));
      } catch {
        // Storage unavailable: the choice just won't persist.
      }
      return next;
    });
  const defaultKinds = (() => {
    const primary = (['metrics', 'tables'] as ContentKind[]).filter((k) => counts[k] > 0);
    if (primary.length) return primary;
    const any = CONTENT_KINDS.map((k) => k.key).filter((k) => counts[k] > 0);
    return any.length ? any : (['metrics', 'tables'] as ContentKind[]);
  })();
  // A remembered choice that's all empty here (e.g. only Metric Views, in a
  // subdomain with none) falls back to the defaults rather than a blank page.
  const shownKinds = pickedKinds && pickedKinds.some((k) => counts[k] > 0) ? pickedKinds : defaultKinds;
  const isShown = (kind: ContentKind) => shownKinds.includes(kind);
  const showHeadings = shownKinds.length > 1;
  const toggleKind = (kind: ContentKind) => {
    const next = isShown(kind) ? shownKinds.filter((k) => k !== kind) : [...shownKinds, kind];
    // Keep at least one kind selected so the page is never blank.
    if (next.length) setPickedKinds(CONTENT_KINDS.map((k) => k.key).filter((k) => next.includes(k)));
  };

  // Views with readable KPIs first; the rest keep their order.
  const sortedMetricViews = [...metricViews].sort(
    (a, b) => Number((b.kpis?.length ?? 0) > 0) - Number((a.kpis?.length ?? 0) > 0),
  );

  // Every section pages the same way; each resets when the scope or its contents change.
  const pageKey = (n: number) => `${domain}|${subdomain}|${n}`;
  const mvPages = usePagination(sortedMetricViews, PAGE_SIZE, pageKey(sortedMetricViews.length));
  const tablePages = usePagination(tables, PAGE_SIZE, pageKey(tables.length));
  const dashboardPages = usePagination(dashboardsList, PAGE_SIZE, pageKey(dashboardsList.length));
  const appPages = usePagination(appsList, PAGE_SIZE, pageKey(appsList.length));

  // Opening a metric view makes sure metric views are shown.
  useEffect(() => {
    if (selectedMetricView) {
      setPickedKinds((prev) => {
        const base = prev ?? defaultKinds;
        return base.includes('metrics') ? prev : ['metrics', ...base];
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
          <div className="flex flex-1 min-w-0 flex-wrap items-center gap-2">
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
                      <span className="text-[10px] text-slate-400 font-semibold">{plural(dom.metric_view_count, 'metric view')}</span>
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
                <span>{subdomain || 'All Subdomains'}</span>
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
                    <span className="text-[10px] text-slate-400">{plural(currentDomainData?.metric_view_count ?? 0, 'metric view')}</span>
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
                      <span className="text-[10px] text-slate-400 font-semibold whitespace-nowrap">{sd.metric_views_count} MV · {plural(sd.tables_count, 'table')}</span>
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

            {/* Search, scoped to whatever is selected */}
            <DiscoverSearch
              alwaysOpen
              value={searchTerm}
              onChange={onSearchChange}
              onSubmitAgentQuery={onSubmitAgentQuery}
              placeholder={`Search in ${subdomain || domain}…`}
              className="flex-1 min-w-[14rem] max-w-md"
              expandedWidth="w-full"
            />
          </div>

        {/* Right side: Accessible and Certified toggles */}
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
              <span className="hidden 2xl:inline">Accessible to me</span>
            </button>
          )}

          <button
            type="button"
            onClick={onToggleCertifiedOnly}
            title="Show only certified assets"
            aria-pressed={showCertifiedOnly}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all cursor-pointer ${
              showCertifiedOnly
                ? 'bg-emerald-50 text-emerald-800 border-emerald-300 shadow-2xs font-bold'
                : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
            <span className="hidden 2xl:inline">Certified Only</span>
          </button>

        </div>
      </div>
    </div>

      {/* SUBDOMAIN FOCUSED SUMMARY (When a subdomain is chosen) */}
      {currentSubdomainMeta && (
        <div className="bg-gradient-to-r from-primary/5 via-white to-slate-50 border border-primary/20 rounded-2xl p-5 shadow-2xs">
          <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
            <div className="space-y-1.5 flex-1">
              <h2 className="text-2xl font-black text-slate-900 tracking-tight">
                {currentSubdomainMeta.name}
              </h2>
              <p className="text-xs text-slate-600 max-w-3xl leading-relaxed">
                {currentSubdomainMeta.description}
              </p>

            </div>

          </div>
        </div>
      )}

      {/* Content switcher (multi-select): Metric Views | Tables & Views | Dashboards | Apps */}
      <div className="space-y-4">
        <div role="group" aria-label="Show asset types" className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {CONTENT_KINDS.map(({ key, label, Icon, on, off }) => {
            const active = isShown(key);
            return (
              <button
                key={key}
                type="button"
                aria-pressed={active}
                onClick={() => toggleKind(key)}
                className={`relative flex items-center gap-3 rounded-2xl border-2 px-4 py-3 text-left transition-all cursor-pointer ${
                  active ? on : `bg-white border-slate-200 hover:border-slate-300 ${off}`
                } ${counts[key] === 0 && !active ? 'opacity-60' : ''}`}
              >
                <Icon className="w-5 h-5 shrink-0" />
                <div className="min-w-0">
                  <div className="text-xl font-black leading-none">{counts[key]}</div>
                  <div className="text-xs font-semibold mt-1 truncate">{label}</div>
                </div>
                {active && <Check className="absolute top-2 right-2 w-3.5 h-3.5" />}
              </button>
            );
          })}
        </div>

        {/* METRIC VIEWS */}
        {isShown('metrics') && (
          <section className="space-y-3 scroll-mt-28">
            {showHeadings && <SectionHeading kind="metrics" count={counts.metrics} />}
            {metricViews.length > 0 ? (
              <>
                <div className={GRID}>
                  {mvPages.pageItems.map((mv) => (
                    <MetricViewCard
                      key={mv.id}
                      asset={mv}
                      isSelected={selectedMetricView?.id === mv.id}
                      onSelect={(asset) => onSelectMetricView(selectedMetricView?.id === asset.id ? null : asset)}
                      onRequestAccess={onRequestAccess}
                    />
                  ))}
                </div>
                <Pagination {...mvPages} onPageChange={mvPages.setPage} />
              </>
            ) : (
              <EmptyNote>
                {showHeadings
                  ? `No metric views in ${subdomain || domain}.`
                  : `No metric views in ${subdomain || domain}. Select Tables & Views to see its data.`}
              </EmptyNote>
            )}
          </section>
        )}

        {/* TABLES & VIEWS */}
        {isShown('tables') && (
          <section className="space-y-3 scroll-mt-28">
            {showHeadings && <SectionHeading kind="tables" count={counts.tables} />}
            {tables.length > 0 ? (
              <>
                <div className={GRID}>
                  {tablePages.pageItems.map((tbl) => (
                    <AssetCard
                      key={tbl.id}
                      asset={tbl}
                      workspaceUrl={workspaceUrl}
                      isSelected={selectedTableId === tbl.id}
                      onSelect={onSelectTable}
                    />
                  ))}
                </div>
                <Pagination {...tablePages} onPageChange={tablePages.setPage} />
              </>
            ) : (
              <EmptyNote>No tables or views in {subdomain || domain}.</EmptyNote>
            )}
          </section>
        )}

        {/* DASHBOARDS */}
        {isShown('dashboards') && (
          <section className="space-y-3 scroll-mt-28">
            {showHeadings && <SectionHeading kind="dashboards" count={counts.dashboards} />}
            {dashboardsList.length > 0 ? (
              <>
                <div className={GRID}>
                  {dashboardPages.pageItems.map((dash) => (
                    <LinkCard
                      key={dash.id}
                      item={dash}
                      Icon={LayoutDashboard}
                      badge="Dashboard"
                      openLabel="Open Dashboard"
                      tone="amber"
                    />
                  ))}
                </div>
                <Pagination {...dashboardPages} onPageChange={dashboardPages.setPage} />
              </>
            ) : (
              <EmptyNote>No dashboards linked to {subdomain || domain}.</EmptyNote>
            )}
          </section>
        )}

        {/* APPS */}
        {isShown('apps') && (
          <section className="space-y-3 scroll-mt-28">
            {showHeadings && <SectionHeading kind="apps" count={counts.apps} />}
            {appsList.length > 0 ? (
              <>
                <div className={GRID}>
                  {appPages.pageItems.map((app) => (
                    <LinkCard key={app.id} item={app} Icon={Box} badge="App" openLabel="Launch App" tone="purple" />
                  ))}
                </div>
                <Pagination {...appPages} onPageChange={appPages.setPage} />
              </>
            ) : (
              <EmptyNote>No apps found for {subdomain || domain}.</EmptyNote>
            )}
          </section>
        )}
      </div>

      {/* Metric view details open in a side panel so the list keeps its place. */}
      {selectedMetricView && (
        <SidePanel label="Metric view details" onClose={() => onSelectMetricView(null)}>
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
        </SidePanel>
      )}
    </div>
  );
}
