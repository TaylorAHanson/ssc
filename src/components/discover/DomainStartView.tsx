import { useRef, useEffect, useMemo } from 'react';
import {
  Truck,
  Landmark,
  ShoppingBag,
  Factory,
  ShieldCheck,
  Database,
  Layers,
  TrendingUp,
  ArrowRight,
  Building2,
  Check,
  ChevronRight,
  Sparkles,
  X,
} from 'lucide-react';
import type { DomainHierarchy, SubdomainMeta } from '../../services/api';
import { scrollContainerOf } from '../../hooks/useViewScroll';
import { CONTENT_KINDS, type ContentKind, type ContentKindCounts } from './contentKinds';
import { PinnedAssetsStrip } from './PinnedAssetsStrip';

interface DomainStartViewProps {
  domains: DomainHierarchy[];
  selectedDomain: string | null;
  onSelectDomain: (domain: string | null) => void;
  selectedSubdomain: string | null;
  onSelectSubdomain: (subdomain: string | null) => void;
  /** `show` opens the view on just those content kinds. */
  onLaunchFullMode: (domain: string, subdomain?: string | null, show?: ContentKind[] | null) => void;
  /** Whole-domain item counts per kind for the selected domain; missing kinds are still loading. */
  kindCounts?: ContentKindCounts;
  /** Opens a pinned asset in the details drawer. */
  onOpenAsset: (assetId: string) => void;
}

/** The shared foundation every other domain builds on; always listed first. */
const ENTERPRISE_DOMAIN = 'Enterprise Data';

// Whole-domain shortcuts, most asked-for first.
const SHORTCUT_ORDER: ContentKind[] = ['legacy', 'tables', 'dashboards', 'metrics', 'apps'];
const SHORTCUT_HINTS: Record<ContentKind, string> = {
  legacy: 'Find the metric view that replaces an old dashboard',
  tables: 'Governed tables and views',
  dashboards: 'Dashboards built on these metric views',
  metrics: 'Governed business metrics and KPIs',
  apps: 'Apps built on this data',
};

function plural(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

function getDomainMeta(domain: string) {
  const d = domain.toLowerCase();
  if (domain === ENTERPRISE_DOMAIN) {
    return {
      icon: Building2,
      accent: 'primary',
      badge: 'bg-primary/10 text-primary border-primary/20',
      activeBorder: 'border-primary ring-2 ring-primary/20 bg-primary/5',
      iconBg: 'bg-primary text-white',
      featured: true,
    };
  }
  if (d.includes('supply')) {
    return {
      icon: Truck,
      accent: 'primary',
      badge: 'bg-primary/10 text-primary border-primary/20',
      activeBorder: 'border-primary ring-2 ring-primary/20 bg-primary/5',
      iconBg: 'bg-primary/10 text-primary',
    };
  }
  if (d.includes('finance')) {
    return {
      icon: Landmark,
      accent: 'indigo',
      badge: 'bg-indigo-50 text-indigo-800 border-indigo-200',
      activeBorder: 'border-indigo-500 ring-2 ring-indigo-500/20 bg-indigo-50/20',
      iconBg: 'bg-indigo-100 text-indigo-700',
    };
  }
  if (d.includes('sales') || d.includes('commercial')) {
    return {
      icon: ShoppingBag,
      accent: 'blue',
      badge: 'bg-blue-50 text-blue-800 border-blue-200',
      activeBorder: 'border-blue-500 ring-2 ring-blue-500/20 bg-blue-50/20',
      iconBg: 'bg-blue-100 text-blue-700',
    };
  }
  if (d.includes('operation') || d.includes('facilit')) {
    return {
      icon: Factory,
      accent: 'amber',
      badge: 'bg-amber-50 text-amber-800 border-amber-200',
      activeBorder: 'border-amber-500 ring-2 ring-amber-500/20 bg-amber-50/20',
      iconBg: 'bg-amber-100 text-amber-700',
    };
  }
  if (d.includes('risk') || d.includes('complian') || d.includes('govern')) {
    return {
      icon: ShieldCheck,
      accent: 'rose',
      badge: 'bg-rose-50 text-rose-800 border-rose-200',
      activeBorder: 'border-rose-500 ring-2 ring-rose-500/20 bg-rose-50/20',
      iconBg: 'bg-rose-100 text-rose-700',
    };
  }
  return {
    icon: Database,
    accent: 'slate',
    badge: 'bg-slate-100 text-slate-800 border-slate-200',
    activeBorder: 'border-slate-800 ring-2 ring-slate-800/10 bg-slate-50/50',
    iconBg: 'bg-slate-200 text-slate-700',
  };
}

export function DomainStartView({
  domains,
  selectedDomain,
  onSelectDomain,
  selectedSubdomain,
  onSelectSubdomain,
  onLaunchFullMode,
  kindCounts = {},
  onOpenAsset,
}: DomainStartViewProps) {
  const orderedDomains = useMemo(
    () => [...domains].sort((a, b) => Number(b.domain === ENTERPRISE_DOMAIN) - Number(a.domain === ENTERPRISE_DOMAIN)),
    [domains],
  );
  const currentDomainData = domains.find((d) => d.domain === selectedDomain);
  // Kinds known to be empty are left out; unknown counts (still loading) still get a shortcut.
  const shortcuts = SHORTCUT_ORDER.map((key) => ({
    ...CONTENT_KINDS.find((k) => k.key === key)!,
    count: kindCounts[key],
  })).filter((s) => s.count !== 0);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const step2Ref = useRef<HTMLDivElement | null>(null);
  const isInitialMount = useRef(true);

  // Smoothly scroll to Step 2 when a domain is selected, or back to top when
  // deselected. Mounting with one already selected (going back) keeps the
  // position the page restored.
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }

    if (selectedDomain && step2Ref.current) {
      const timer = setTimeout(() => {
        step2Ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 100);
      return () => clearTimeout(timer);
    } else if (!selectedDomain) {
      scrollContainerOf(rootRef.current).scrollTo({ top: 0, behavior: 'smooth' });
    }
  }, [selectedDomain]);

  return (
    <div
      ref={rootRef}
      className={`max-w-6xl mx-auto space-y-8 transition-all duration-500 ease-out ${
        selectedDomain
          ? 'pt-2 sm:pt-4 pb-28'
          : 'pt-4 sm:pt-8 pb-16 min-h-[55vh]'
      }`}
    >
      {/* Clean Hero Title */}
      <div className="text-center pt-2 pb-1">
        <h2 className="text-3xl font-extrabold text-slate-900 tracking-tight sm:text-4xl">
          What domain are you interested in?
        </h2>
      </div>

      <PinnedAssetsStrip onOpen={onOpenAsset} />

      {/* Step 1: Big Domain Cards / Boxes */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {orderedDomains.map((dom) => {
          const isSelected = dom.domain === selectedDomain;
          const config = getDomainMeta(dom.domain);
          const Icon = config.icon;
          const opensDirectly = dom.has_subdomains === false;

          return (
            <div
              key={dom.domain}
              onClick={() => {
                if (opensDirectly) {
                  // Nothing to choose between: open the domain's assets directly.
                  onLaunchFullMode(dom.domain, null);
                } else if (isSelected) {
                  // Toggle off
                  onSelectDomain(null);
                  onSelectSubdomain(null);
                } else {
                  onSelectDomain(dom.domain);
                  onSelectSubdomain(null);
                }
              }}
              className={`relative rounded-2xl p-6 border transition-all cursor-pointer group flex flex-col justify-between ${
                isSelected
                  ? 'border-primary ring-2 ring-primary/20 bg-primary/5 shadow-md'
                  : config.featured
                    ? 'bg-gradient-to-br from-primary/10 via-white to-white border-primary/30 shadow-sm hover:border-primary/60 hover:shadow-md'
                    : 'bg-white border-slate-200 hover:border-primary/40 hover:shadow-md'
              }`}
            >
              {config.featured && (
                <span aria-hidden className="absolute inset-x-6 top-0 h-1 rounded-b-full bg-gradient-to-r from-primary to-primary/30" />
              )}
              <div>
                <div className="flex items-start justify-between gap-3 mb-4">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div className={`w-11 h-11 shrink-0 rounded-xl flex items-center justify-center ${config.iconBg} shadow-2xs`}>
                      <Icon className="w-5 h-5" />
                    </div>
                  </div>
                  {isSelected ? (
                    <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
                      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-slate-900 text-white text-[11px] font-bold shadow-xs">
                        <Check className="w-3 h-3 stroke-[3]" /> Selected
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          onSelectDomain(null);
                          onSelectSubdomain(null);
                        }}
                        className="p-1 rounded-full bg-slate-200 hover:bg-slate-300 text-slate-700 hover:text-slate-900 transition-colors cursor-pointer"
                        title="Deselect domain"
                      >
                        <X className="w-3.5 h-3.5 stroke-[2.5]" />
                      </button>
                    </div>
                  ) : (
                    <span className="text-[11px] font-semibold text-slate-400 group-hover:text-slate-600 transition-colors whitespace-nowrap">
                      {opensDirectly ? 'Click to open' : 'Click to choose'}
                    </span>
                  )}
                </div>

                {config.featured && (
                  <span className="mb-2 inline-flex items-center gap-1 px-2 py-0.5 rounded-full border bg-primary/10 text-primary border-primary/20 text-[10px] font-bold uppercase tracking-wider whitespace-nowrap">
                    <Sparkles className="w-3 h-3" /> Shared foundation
                  </span>
                )}
                <h3 className="text-xl font-bold text-slate-900 group-hover:text-primary transition-colors mb-1.5">
                  {dom.domain}
                </h3>
                <p className="text-xs text-slate-600 line-clamp-2 leading-relaxed mb-4">
                  {dom.description}
                </p>
              </div>

              {/* Bottom Meta Stats */}
              <div className={`pt-4 border-t flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 text-xs text-slate-500 ${config.featured ? 'border-primary/10' : 'border-slate-100'}`}>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-medium">
                  {!opensDirectly && (
                    <>
                      <span className="text-slate-700 font-semibold inline-flex items-center gap-1 whitespace-nowrap">
                        <Layers className="w-3.5 h-3.5 text-slate-400" />
                        {plural(dom.subdomain_count, 'subdomain')}
                      </span>
                      <span className="text-slate-300">•</span>
                    </>
                  )}
                  <span className="inline-flex items-center gap-1 text-primary font-semibold whitespace-nowrap">
                    <TrendingUp className="w-3.5 h-3.5" />
                    {plural(dom.metric_view_count, 'metric view')}
                  </span>
                </div>

                <div className="inline-flex items-center gap-1 text-slate-400 group-hover:text-primary group-hover:translate-x-0.5 transition-all font-semibold text-xs whitespace-nowrap">
                  <span>{isSelected ? 'Change' : opensDirectly ? 'Open' : 'Select'}</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Step 2: subdomains on the left, whole-domain shortcuts on the right */}
      {currentDomainData && (
        <div
          ref={step2Ref}
          className="mt-8 pt-6 border-t border-slate-200 animate-in fade-in slide-in-from-top-6 duration-300 space-y-4 scroll-mt-4"
        >
          <div>
            <div className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-primary mb-1">
              <span className="px-2 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">Step 2</span>
              <span className="text-slate-400">•</span>
              <span>Choose where to start</span>
            </div>
            <h3 className="text-2xl font-bold text-slate-900 tracking-tight flex items-center gap-2">
              <span>Explore {currentDomainData.domain}</span>
              <button
                type="button"
                onClick={() => {
                  onSelectDomain(null);
                  onSelectSubdomain(null);
                }}
                className="inline-flex items-center gap-1 text-xs font-semibold text-slate-500 hover:text-slate-800 bg-slate-100 hover:bg-slate-200 px-2 py-0.5 rounded-lg transition-colors cursor-pointer ml-1"
                title="Clear domain selection"
              >
                <X className="w-3.5 h-3.5" />
                <span>Deselect</span>
              </button>
            </h3>
            <p className="text-xs text-slate-500">
              Pick a subdomain, or open the whole domain on the kind of content you need.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_18rem] lg:grid-cols-[minmax(0,1fr)_20rem] gap-5 items-start">
            {/* Subdomains */}
            <section aria-labelledby="discover-step2-subdomains" className="min-w-0">
              <h4 id="discover-step2-subdomains" className="mb-2 px-1 text-[11px] font-bold uppercase tracking-wider text-slate-400">
                Subdomains ({currentDomainData.subdomains.length})
              </h4>
              {currentDomainData.subdomains.length > 0 ? (
                <ul className="bg-white rounded-2xl border border-slate-200 shadow-2xs divide-y divide-slate-100 overflow-hidden">
                  {currentDomainData.subdomains.map((sd: SubdomainMeta) => {
                    const isSelected = selectedSubdomain === sd.name;
                    const counts = (
                      <>
                        {sd.metric_views_count > 0 && (
                          <>
                            <span className="inline-flex items-center gap-1 text-primary font-semibold">
                              <TrendingUp className="w-3 h-3" />
                              {plural(sd.metric_views_count, 'metric view')}
                            </span>
                            <span className="text-slate-300">·</span>
                          </>
                        )}
                        <span className="inline-flex items-center gap-1">
                          <Database className="w-3 h-3 text-slate-400" />
                          {plural(sd.tables_count, 'table')}
                        </span>
                      </>
                    );

                    return (
                      <li key={sd.name}>
                        <button
                          type="button"
                          onClick={() => {
                            onSelectSubdomain(sd.name);
                            onLaunchFullMode(currentDomainData.domain, sd.name);
                          }}
                          className={`group w-full text-left flex items-center gap-4 px-4 py-3 transition-colors cursor-pointer ${
                            isSelected ? 'bg-primary/5' : 'hover:bg-slate-50'
                          }`}
                        >
                          <div className="min-w-0 flex-1">
                            <div className="text-sm font-bold text-slate-900 group-hover:text-primary transition-colors truncate">
                              {sd.name}
                            </div>
                            <p className="text-xs text-slate-500 truncate">{sd.description}</p>
                            <div className="sm:hidden mt-1 flex items-center gap-1.5 text-[11px] font-medium text-slate-500 whitespace-nowrap">
                              {counts}
                            </div>
                          </div>
                          <div className="hidden sm:flex items-center gap-1.5 text-[11px] font-medium text-slate-500 whitespace-nowrap shrink-0">
                            {counts}
                          </div>
                          <ChevronRight className="w-4 h-4 shrink-0 text-slate-300 group-hover:text-primary group-hover:translate-x-0.5 transition-all" />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-xs text-slate-500 bg-slate-50 rounded-xl border border-slate-200 px-4 py-3">
                  No subdomains yet — browse the whole domain instead.
                </p>
              )}
            </section>

            {/* Whole-domain shortcuts, one per content kind */}
            <aside aria-labelledby="discover-step2-shortcuts" className="min-w-0">
              <h4 id="discover-step2-shortcuts" className="mb-2 px-1 text-[11px] font-bold uppercase tracking-wider text-slate-400 truncate">
                All of {currentDomainData.domain}
              </h4>
              <div className="bg-white rounded-2xl border border-slate-200 shadow-2xs p-2">
                <ul className="space-y-0.5">
                  {shortcuts.map(({ key, label, Icon, off, count }) => (
                    <li key={key}>
                      <button
                        type="button"
                        onClick={() => onLaunchFullMode(currentDomainData.domain, null, [key])}
                        className="group w-full flex items-center gap-3 rounded-xl px-3 py-2.5 text-left hover:bg-slate-50 transition-colors cursor-pointer"
                      >
                        <span className={`w-8 h-8 shrink-0 rounded-lg flex items-center justify-center bg-slate-50 border border-slate-100 ${off}`}>
                          <Icon className="w-4 h-4" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-semibold text-slate-900 group-hover:text-primary transition-colors truncate">
                            {label}
                          </span>
                          <span className="block text-[11px] text-slate-500 truncate">{SHORTCUT_HINTS[key]}</span>
                        </span>
                        {count !== undefined && (
                          <span className="text-xs font-bold text-slate-500 tabular-nums">{count}</span>
                        )}
                        <ChevronRight className="w-4 h-4 shrink-0 text-slate-300 group-hover:text-primary group-hover:translate-x-0.5 transition-all" />
                      </button>
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  onClick={() => onLaunchFullMode(currentDomainData.domain, null)}
                  className={`w-full inline-flex items-center justify-between gap-2 px-4 py-2.5 rounded-xl bg-slate-900 hover:bg-slate-800 text-white text-xs font-semibold shadow-sm transition-all cursor-pointer ${
                    shortcuts.length ? 'mt-2' : ''
                  }`}
                >
                  <span className="truncate">Explore all {currentDomainData.domain}</span>
                  <ArrowRight className="w-3.5 h-3.5 shrink-0" />
                </button>
              </div>
            </aside>
          </div>
        </div>
      )}
    </div>
  );
}
