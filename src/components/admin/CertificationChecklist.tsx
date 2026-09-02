import { useState } from 'react';
import { CheckCircle2, XCircle, ClipboardList } from 'lucide-react';

// A single enriched rule row. This is the shape both the Enforcement Sentinel
// run report and the ODCS certification modal feed in, so the checklist renders
// identically in both places (DRY).
export interface ChecklistRuleRow {
  id?: string;
  description?: string;
  category?: string;
  passed: boolean;
  messages?: string[];
  violations?: string[];
  severity?: string;
  policy?: string;
  resource_id?: string;
  resource_type?: string;
  resource?: { name?: string; tags?: string[] | Record<string, string | number> };
}

type Filter = 'all' | 'pass' | 'violation';

const severityRank: Record<string, number> = {
  CRITICAL: 4,
  HIGH: 3,
  MEDIUM: 2,
  LOW: 1,
  NONE: 0,
};

/**
 * The audit checklist (pass + fail, per rule) shared by the Enforcement Sentinel
 * run report and the Data Certification (ODCS) page. Renders its own pass/all/
 * violation filter pills plus the results table so callers only supply rows.
 */
export function CertificationChecklist({ ruleRows }: { ruleRows: ChecklistRuleRow[] }) {
  const [filter, setFilter] = useState<Filter>('all');

  const passCount = ruleRows.filter((r) => r.passed).length;
  const violationCount = ruleRows.filter((r) => !r.passed).length;

  const filtered = ruleRows.filter((r) => {
    if (filter === 'pass') return r.passed;
    if (filter === 'violation') return !r.passed;
    return true;
  });

  // Violations first (by severity desc), then passes (by resource / policy).
  const sorted = [...filtered].sort((a, b) => {
    if (a.passed !== b.passed) return a.passed ? 1 : -1;
    if (!a.passed) {
      const sa = severityRank[a.severity || 'NONE'] || 0;
      const sb = severityRank[b.severity || 'NONE'] || 0;
      if (sa !== sb) return sb - sa;
    }
    const ra = (a.resource_id || '').toString();
    const rb = (b.resource_id || '').toString();
    if (ra !== rb) return ra.localeCompare(rb);
    return (a.policy || '').localeCompare(b.policy || '');
  });

  return (
    <div className="flex flex-col">
      <div className="sticky top-0 z-20 flex items-center gap-2 h-11 border-b border-gray-200 bg-gray-50/95 backdrop-blur-sm px-3">
        {(
          [
            { id: 'all', label: `All (${ruleRows.length})` },
            { id: 'pass', label: `Passed (${passCount})` },
            { id: 'violation', label: `Violations (${violationCount})` },
          ] as { id: Filter; label: string }[]
        ).map((opt) => (
          <button
            key={opt.id}
            onClick={() => setFilter(opt.id)}
            className={`px-3 py-1 text-sm font-medium rounded-md whitespace-nowrap transition-colors flex-shrink-0 ${
              filter === opt.id
                ? 'bg-white text-gray-900 shadow-sm ring-1 ring-gray-200'
                : 'text-gray-500 hover:text-gray-900 hover:bg-gray-100'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      <div className="p-0">
        {ruleRows.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-12 text-center">
            <ClipboardList className="w-12 h-12 text-gray-400 mb-4" />
            <h3 className="text-lg font-medium text-gray-900">No checklist data available</h3>
            <p className="text-gray-500 text-sm mt-1 max-w-sm">
              This dataset has not recorded per-rule evaluations yet. Run the Enforcement Sentinel
              to capture a full audit checklist.
            </p>
          </div>
        ) : sorted.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-12 text-center">
            <CheckCircle2 className="w-12 h-12 text-green-400 mb-4" />
            <h3 className="text-lg font-medium text-gray-900">Nothing matches this filter</h3>
            <p className="text-gray-500 text-sm mt-1 max-w-sm">
              Try switching the filter above to see other checks.
            </p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-white sticky top-11 z-10 text-gray-500 font-medium border-b border-gray-200 shadow-sm">
              <tr>
                <th className="p-3 px-4 text-left w-28">Result</th>
                <th className="p-3 text-left">Resource</th>
                <th className="p-3 text-left">Category</th>
                <th className="p-3 text-left">Check</th>
                <th className="p-3 text-left w-24">Severity</th>
                <th className="p-3 text-left w-1/3">Notes</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {sorted.map((r, idx) => {
                const tags = r.resource?.tags;
                const tagList = Array.isArray(tags)
                  ? tags
                  : tags && typeof tags === 'object'
                  ? Object.entries(tags).map(([k, v]) => `${k}: ${v}`)
                  : [];
                const msgs: string[] = (r.messages || r.violations || []) as string[];
                return (
                  <tr key={idx} className="hover:bg-gray-50/60 align-top">
                    <td className="p-3 px-4">
                      {r.passed ? (
                        <span className="inline-flex items-center gap-1 text-[10px] uppercase font-bold px-2 py-1 rounded-full bg-green-50 text-green-700 border border-green-200">
                          <CheckCircle2 className="w-3 h-3" /> Pass
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-[10px] uppercase font-bold px-2 py-1 rounded-full bg-red-50 text-red-700 border border-red-200">
                          <XCircle className="w-3 h-3" /> Violation
                        </span>
                      )}
                    </td>
                    <td className="p-3">
                      <div className="flex flex-col gap-1">
                        <span className="text-[10px] text-gray-700 font-semibold uppercase tracking-wider">
                          {r.resource_type}
                        </span>
                        <span className="font-medium text-gray-900">
                          {r.resource?.name || r.resource_id}
                        </span>
                        <span className="font-mono text-[10px] text-gray-500 break-all">
                          {r.resource_id}
                        </span>
                        {tagList.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1">
                            {tagList.slice(0, 4).map((t: string, i: number) => (
                              <span
                                key={i}
                                className="inline-block text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 border border-gray-200"
                              >
                                {t}
                              </span>
                            ))}
                            {tagList.length > 4 && (
                              <span className="inline-block text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 border border-gray-200">
                                +{tagList.length - 4}
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="p-3 text-gray-700">{r.category || '—'}</td>
                    <td className="p-3 text-gray-700">{r.description || r.id}</td>
                    <td className="p-3">
                      {!r.passed ? (
                        <span
                          className={`text-[10px] uppercase font-bold px-2 py-1 rounded-full ${
                            r.severity === 'CRITICAL'
                              ? 'bg-red-100 text-red-800 border border-red-200'
                              : r.severity === 'HIGH'
                              ? 'bg-orange-100 text-orange-800 border border-orange-200'
                              : r.severity === 'MEDIUM'
                              ? 'bg-yellow-100 text-yellow-800 border border-yellow-200'
                              : 'bg-gray-100 text-gray-800 border border-gray-200'
                          }`}
                        >
                          {r.severity}
                        </span>
                      ) : (
                        <span className="text-gray-300 text-xs">—</span>
                      )}
                    </td>
                    <td className="p-3 text-xs text-gray-600 leading-relaxed break-words">
                      {!r.passed && msgs.length > 0 ? (
                        <ul className="space-y-0.5">
                          {msgs.slice(0, 3).map((v, vi) => (
                            <li key={vi}>• {v}</li>
                          ))}
                          {msgs.length > 3 && (
                            <li className="italic text-gray-400">+{msgs.length - 3} more</li>
                          )}
                        </ul>
                      ) : (
                        <span className="text-gray-300">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export default CertificationChecklist;
