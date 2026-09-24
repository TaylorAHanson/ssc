import { useState, useMemo } from 'react';
import { ArrowLeftRight, CheckCircle2, ExternalLink, Loader, TrendingUp, XCircle } from 'lucide-react';
import type { LegacyMapping, LegacyStatus } from '../../services/api';
import { LEGACY_STATUSES, legacyMatches, prettyMetricViewName } from './legacyMappings';

const STATUS_STYLES: Record<LegacyStatus, { Icon: typeof CheckCircle2; className: string }> = {
  Active: { Icon: CheckCircle2, className: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  Migrating: { Icon: Loader, className: 'bg-amber-50 text-amber-800 border-amber-200' },
  Deprecated: { Icon: XCircle, className: 'bg-rose-50 text-rose-700 border-rose-200' },
};

export function LegacyStatusBadge({ status }: { status: LegacyStatus }) {
  const { Icon, className } = STATUS_STYLES[status] ?? STATUS_STYLES.Active;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-semibold whitespace-nowrap ${className}`}>
      <Icon className="w-3 h-3" />
      {status}
    </span>
  );
}

interface LegacyMappingTableProps {
  /** Mappings in the current domain / subdomain. */
  mappings: LegacyMapping[];
  /** The page's search box; narrows the rows. */
  searchTerm?: string;
  /** Hide the subdomain column once a subdomain is picked. */
  showSubdomain?: boolean;
  onOpenMetricView: (metricViewId: string) => void;
}

/** "I used dashboard X — what replaced it?" Legacy dashboards and their governed metric views. */
export function LegacyMappingTable({ mappings, searchTerm = '', showSubdomain = true, onOpenMetricView }: LegacyMappingTableProps) {
  const [status, setStatus] = useState<LegacyStatus | null>(null);

  const matching = useMemo(() => mappings.filter((m) => legacyMatches(m, searchTerm)), [mappings, searchTerm]);
  const counts = useMemo(() => {
    const c: Record<LegacyStatus, number> = { Active: 0, Migrating: 0, Deprecated: 0 };
    matching.forEach((m) => (c[m.status] = (c[m.status] ?? 0) + 1));
    return c;
  }, [matching]);
  const rows = status ? matching.filter((m) => m.status === status) : matching;

  return (
    <div className="space-y-3">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <p className="text-xs text-slate-500">
          Find a dashboard you used before and see the governed metric view that replaces it.
        </p>
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter by status">
          {LEGACY_STATUSES.map((s) => {
            const { Icon, className } = STATUS_STYLES[s];
            const active = status === s;
            return (
              <button
                key={s}
                type="button"
                aria-pressed={active}
                onClick={() => setStatus(active ? null : s)}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-semibold transition-all cursor-pointer ${className} ${
                  active ? 'ring-2 ring-offset-1 ring-current' : status ? 'opacity-50 hover:opacity-100' : ''
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {s}: {counts[s]}
              </button>
            );
          })}
        </div>
      </div>

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-xs">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200 text-slate-500 font-semibold uppercase tracking-wider text-[11px]">
              <th className="py-3 px-4">Dashboard (legacy)</th>
              <th className="py-3 px-4">Status</th>
              <th className="py-3 px-4">Owner</th>
              <th className="py-3 px-4">Metric view</th>
              {showSubdomain && <th className="py-3 px-4">Subdomain</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((m) => (
              <tr key={m.id} className="hover:bg-slate-50/70 transition-colors align-top">
                <td className="py-3 px-4 min-w-[14rem]">
                  <div className="font-semibold text-slate-900 text-sm flex items-center gap-1.5">
                    <span>{m.dashboard}</span>
                    {m.url && (
                      <a
                        href={m.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-slate-400 hover:text-slate-600"
                        title="Open the legacy dashboard"
                      >
                        <ExternalLink className="w-3 h-3" />
                      </a>
                    )}
                  </div>
                  {m.description && <div className="text-[11px] text-slate-500 mt-0.5 line-clamp-1">{m.description}</div>}
                </td>
                <td className="py-3 px-4">
                  <LegacyStatusBadge status={m.status} />
                </td>
                <td className="py-3 px-4 text-slate-700 font-medium">{m.owner || <span className="text-slate-300">—</span>}</td>
                <td className="py-3 px-4 min-w-[12rem]">
                  <button
                    type="button"
                    onClick={() => onOpenMetricView(m.metric_view_id)}
                    className="group inline-flex items-start gap-2 text-left cursor-pointer"
                    title={`Open ${m.metric_view_id}`}
                  >
                    <span className="mt-0.5 p-1 rounded-md bg-primary/10 text-primary">
                      <TrendingUp className="w-3 h-3" />
                    </span>
                    <span>
                      <span className="block font-mono font-semibold text-slate-800 group-hover:text-primary">
                        {prettyMetricViewName(m.metric_view)}
                      </span>
                      {m.metric_view_description && (
                        <span className="text-[11px] text-slate-500 line-clamp-1 max-w-md">{m.metric_view_description}</span>
                      )}
                    </span>
                  </button>
                </td>
                {showSubdomain && <td className="py-3 px-4 text-primary font-medium whitespace-nowrap">{m.subdomain}</td>}
              </tr>
            ))}
          </tbody>
        </table>

        {rows.length === 0 && (
          <div className="flex flex-col items-center gap-1 py-10 text-slate-400 text-xs">
            <ArrowLeftRight className="w-4 h-4" />
            {searchTerm.trim() ? `No legacy dashboards match “${searchTerm.trim()}”.` : 'No legacy dashboards with this status.'}
          </div>
        )}
      </div>
    </div>
  );
}
