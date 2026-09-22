import { useRef, useEffect } from 'react';
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
  Check,
  X,
} from 'lucide-react';
import type { DomainHierarchy, SubdomainMeta } from '../../services/api';

interface DomainStartViewProps {
  domains: DomainHierarchy[];
  selectedDomain: string | null;
  onSelectDomain: (domain: string | null) => void;
  selectedSubdomain: string | null;
  onSelectSubdomain: (subdomain: string | null) => void;
  onLaunchFullMode: (domain: string, subdomain?: string | null) => void;
}

function getDomainMeta(domain: string) {
  const d = domain.toLowerCase();
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
}: DomainStartViewProps) {
  const currentDomainData = domains.find((d) => d.domain === selectedDomain);
  const step2Ref = useRef<HTMLDivElement | null>(null);
  const isInitialMount = useRef(true);

  // Smoothly scroll to Step 2 when a domain is selected, or back to top when deselected
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      if (selectedDomain && step2Ref.current) {
        step2Ref.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      return;
    }

    if (selectedDomain && step2Ref.current) {
      const timer = setTimeout(() => {
        step2Ref.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 100);
      return () => clearTimeout(timer);
    } else if (!selectedDomain) {
      window.scrollTo({ top: 0, behavior: 'smooth' });
      document.querySelector('main')?.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }, [selectedDomain]);

  return (
    <div
      className={`max-w-6xl mx-auto space-y-8 transition-all duration-500 ease-out ${
        selectedDomain
          ? 'pt-2 sm:pt-4 pb-28'
          : 'pt-10 sm:pt-20 lg:pt-28 pb-16 min-h-[55vh]'
      }`}
    >
      {/* Clean Hero Title */}
      <div className="text-center pt-2 pb-1">
        <h2 className="text-3xl font-extrabold text-slate-900 tracking-tight sm:text-4xl">
          What domain are you interested in?
        </h2>
      </div>

      {/* Step 1: Big Domain Cards / Boxes */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {domains.map((dom) => {
          const isSelected = dom.domain === selectedDomain;
          const config = getDomainMeta(dom.domain);
          const Icon = config.icon;

          return (
            <div
              key={dom.domain}
              onClick={() => {
                if (isSelected) {
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
                  : 'bg-white border-slate-200 hover:border-primary/40 hover:shadow-md'
              }`}
            >
              <div>
                <div className="flex items-start justify-between gap-3 mb-4">
                  <div className={`w-11 h-11 rounded-xl flex items-center justify-center ${config.iconBg} shadow-2xs`}>
                    <Icon className="w-5 h-5" />
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
                    <span className="text-[11px] font-semibold text-slate-400 group-hover:text-slate-600 transition-colors">
                      Click to choose
                    </span>
                  )}
                </div>

                <h3 className="text-xl font-bold text-slate-900 group-hover:text-primary transition-colors mb-1.5">
                  {dom.domain}
                </h3>
                <p className="text-xs text-slate-600 line-clamp-2 leading-relaxed mb-4">
                  {dom.description}
                </p>
              </div>

              {/* Bottom Meta Stats */}
              <div className="pt-4 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
                <div className="flex items-center gap-3 font-medium">
                  <span className="text-slate-700 font-semibold inline-flex items-center gap-1">
                    <Layers className="w-3.5 h-3.5 text-slate-400" />
                    {dom.subdomain_count} Subdomains
                  </span>
                  <span className="text-slate-300">•</span>
                  <span className="inline-flex items-center gap-1 text-primary font-semibold">
                    <TrendingUp className="w-3.5 h-3.5" />
                    {dom.metric_view_count} Metrics
                  </span>
                </div>

                <div className="inline-flex items-center gap-1 text-slate-400 group-hover:text-primary group-hover:translate-x-0.5 transition-all font-semibold text-xs">
                  <span>{isSelected ? 'Change' : 'Select'}</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Step 2: Slide-Out / Revealed Subdomains ONLY when a domain is selected */}
      {currentDomainData && (
        <div
          ref={step2Ref}
          className="mt-8 pt-6 border-t border-slate-200 animate-in fade-in slide-in-from-top-6 duration-300 space-y-4"
        >
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <div className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-primary mb-1">
                <span className="px-2 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">Step 2</span>
                <span className="text-slate-400">•</span>
                <span>Choose a Subdomain</span>
              </div>
              <h3 className="text-2xl font-bold text-slate-900 tracking-tight flex items-center gap-2">
                <span>Subdomains in {currentDomainData.domain}</span>
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
                Pick a business subdomain to jump directly into its governed metrics and Lakehouse tables.
              </p>
            </div>

            <button
              type="button"
              onClick={() => onLaunchFullMode(currentDomainData.domain, null)}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-slate-900 hover:bg-slate-800 text-white text-xs font-semibold shadow-sm transition-all cursor-pointer self-start sm:self-center"
            >
              <span>Explore All {currentDomainData.domain} ({currentDomainData.metric_view_count} metrics)</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* Subdomain Cards Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 pt-1">
            {currentDomainData.subdomains.map((sd: SubdomainMeta) => {
              const isSelected = selectedSubdomain === sd.name;

              return (
                <div
                  key={sd.name}
                  onClick={() => {
                    onSelectSubdomain(sd.name);
                    onLaunchFullMode(currentDomainData.domain, sd.name);
                  }}
                  className={`relative rounded-xl p-5 border transition-all cursor-pointer group flex flex-col justify-between ${
                    isSelected
                      ? 'bg-primary/5 border-primary shadow-sm ring-2 ring-primary/20'
                      : 'bg-white border-slate-200 hover:border-primary/40 hover:shadow-md'
                  }`}
                >
                  <div>
                    <div className="flex items-start justify-between gap-2 mb-2">
                      <h4 className="font-bold text-slate-900 group-hover:text-primary transition-colors text-base flex items-center gap-2">
                        {sd.name}
                      </h4>
                      <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-primary group-hover:translate-x-0.5 transition-transform shrink-0">
                        Launch <ArrowRight className="w-3 h-3" />
                      </span>
                    </div>

                    <p className="text-xs text-slate-600 mb-3 line-clamp-2 leading-relaxed">
                      {sd.description}
                    </p>

                    {/* Headline KPIs if available */}
                    {sd.kpis && sd.kpis.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 mb-3">
                        {sd.kpis.slice(0, 2).map((kpi, idx) => (
                          <div
                            key={idx}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-50 border border-slate-200 text-[11px]"
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

                  {/* Footer details */}
                  <div className="pt-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
                    <div className="flex items-center gap-2 font-medium">
                      {sd.metric_views_count > 0 ? (
                        <>
                          <span className="text-primary font-semibold inline-flex items-center gap-1">
                            <TrendingUp className="w-3 h-3" />
                            {sd.metric_views_count} metrics
                          </span>
                          <span className="text-slate-300">•</span>
                        </>
                      ) : null}
                      <span className="inline-flex items-center gap-1 text-slate-600">
                        <Database className="w-3 h-3 text-slate-400" />
                        {sd.tables_count} tables
                      </span>
                    </div>

                    <span className="text-[11px] font-semibold text-slate-400 group-hover:text-primary transition-colors">
                      Full view &rarr;
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
