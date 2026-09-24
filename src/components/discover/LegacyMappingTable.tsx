import { useState, useMemo } from 'react';
import {
  Search,
  TrendingUp,
  LayoutDashboard,
  ChevronRight,
} from 'lucide-react';
import type { LegacyMapping } from '../../services/api';

interface LegacyMappingTableProps {
  mappings: LegacyMapping[];
  onSelectMetricView?: (metricViewName: string) => void;
}

export function LegacyMappingTable({ mappings, onSelectMetricView }: LegacyMappingTableProps) {
  const [selectedStatus, setSelectedStatus] = useState<'All' | 'Active' | 'Migrating' | 'Deprecated'>('All');
  const [searchTerm, setSearchTerm] = useState('');

  const statusCounts = useMemo(() => {
    const counts = { All: mappings.length, Active: 0, Migrating: 0, Deprecated: 0 };
    for (const m of mappings) {
      if (m.status === 'Active') counts.Active++;
      else if (m.status === 'Migrating') counts.Migrating++;
      else if (m.status === 'Deprecated') counts.Deprecated++;
    }
    return counts;
  }, [mappings]);

  const filteredMappings = useMemo(() => {
    return mappings.filter((m) => {
      if (selectedStatus !== 'All' && m.status !== selectedStatus) {
        return false;
      }
      if (searchTerm.trim()) {
        const term = searchTerm.toLowerCase();
        const hay = `${m.dashboard} ${m.owner} ${m.metric_view} ${m.subdomain} ${m.domain} ${m.description}`.toLowerCase();
        if (!hay.includes(term)) return false;
      }
      return true;
    });
  }, [mappings, selectedStatus, searchTerm]);

  return (
    <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm space-y-5">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-slate-100">
        <div>
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <LayoutDashboard className="w-5 h-5 text-indigo-600" />
            Legacy Dashboard Mapping
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Tracking migration of legacy reports to metric views
          </p>
        </div>

        <div className="text-xs font-semibold text-slate-500 bg-slate-50 px-3 py-1.5 rounded-lg border border-slate-200 self-start sm:self-auto">
          <span className="text-slate-900 font-bold">{filteredMappings.length}</span> of {mappings.length} mappings
        </div>
      </div>

      {/* Filter Bar: Status Pills & Search */}
      <div className="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-3">
        {/* Status Filter Pills */}
        <div className="inline-flex items-center gap-1.5 p-1 bg-slate-100 rounded-xl border border-slate-200/80">
          {(['All', 'Active', 'Migrating', 'Deprecated'] as const).map((st) => {
            const isSelected = selectedStatus === st;
            return (
              <button
                key={st}
                type="button"
                onClick={() => setSelectedStatus(st)}
                className={`inline-flex items-center gap-1.5 px-3 py-1 text-xs font-semibold rounded-lg transition-all ${
                  isSelected
                    ? 'bg-white text-slate-900 shadow-sm border border-slate-200'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                <span>{st}</span>
                <span
                  className={`text-[10px] px-1.5 py-0.2 rounded-full font-bold ${
                    isSelected ? 'bg-slate-100 text-slate-700' : 'text-slate-400'
                  }`}
                >
                  {statusCounts[st]}
                </span>
              </button>
            );
          })}
        </div>

        {/* Search Input */}
        <div className="relative max-w-xs w-full">
          <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Filter mappings, owners, metric views..."
            className="w-full pl-9 pr-3 py-1.5 text-xs bg-slate-50 border border-slate-200 rounded-lg focus:bg-white focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500"
          />
        </div>
      </div>

      {/* Mappings Table */}
      <div className="overflow-x-auto rounded-xl border border-slate-200 shadow-sm">
        <table className="w-full text-left text-xs border-collapse">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold uppercase tracking-wider text-[11px]">
              <th className="py-3 px-4">Dashboard (Legacy)</th>
              <th className="py-3 px-4">Status</th>
              <th className="py-3 px-4">Owner</th>
              <th className="py-3 px-4">Metric View</th>
              <th className="py-3 px-4">Subdomain</th>
              <th className="py-3 px-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {filteredMappings.map((m) => {
              const statusBadge =
                m.status === 'Active' ? (
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full font-semibold text-[11px] bg-emerald-50 text-emerald-700 border border-emerald-200">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                    Active
                  </span>
                ) : m.status === 'Migrating' ? (
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full font-semibold text-[11px] bg-amber-50 text-amber-800 border border-amber-200">
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
                    Migrating
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full font-semibold text-[11px] bg-slate-100 text-slate-600 border border-slate-200">
                    <span className="w-1.5 h-1.5 rounded-full bg-slate-400" />
                    Deprecated
                  </span>
                );

              return (
                <tr key={m.id} className="hover:bg-slate-50/70 transition-colors">
                  <td className="py-3 px-4">
                    <div className="font-bold text-slate-900 text-sm flex items-center gap-1.5">
                      <LayoutDashboard className="w-3.5 h-3.5 text-slate-400" />
                      {m.dashboard}
                    </div>
                    {m.description && (
                      <div className="text-[11px] text-slate-500 mt-0.5 line-clamp-1">
                        {m.description}
                      </div>
                    )}
                  </td>
                  <td className="py-3 px-4">{statusBadge}</td>
                  <td className="py-3 px-4 font-medium text-slate-700">{m.owner}</td>
                  <td className="py-3 px-4">
                    <button
                      type="button"
                      onClick={() => {
                        if (onSelectMetricView) onSelectMetricView(m.metric_view);
                      }}
                      className="inline-flex items-center gap-1 font-mono text-xs px-2.5 py-1 rounded-md bg-emerald-50 text-emerald-800 border border-emerald-200/60 font-semibold hover:bg-emerald-100 transition-colors"
                    >
                      <TrendingUp className="w-3 h-3 text-emerald-600" />
                      <span>{m.metric_view.replace(/^(metric_|sem_)/, '').replace(/_metric_view$/, '')}</span>
                    </button>
                  </td>
                  <td className="py-3 px-4">
                    <span className="text-slate-600 font-medium">{m.subdomain}</span>
                    <div className="text-[10px] text-slate-400">{m.domain}</div>
                  </td>
                  <td className="py-3 px-4 text-right">
                    <button
                      type="button"
                      onClick={() => {
                        if (onSelectMetricView) onSelectMetricView(m.metric_view);
                      }}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-700 hover:text-emerald-800"
                    >
                      <span>View Metric</span>
                      <ChevronRight className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {filteredMappings.length === 0 && (
          <div className="text-center py-10 text-slate-400 text-xs">
            No mappings match the selected status or filter.
          </div>
        )}
      </div>
    </div>
  );
}
