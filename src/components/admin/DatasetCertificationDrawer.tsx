import { useEffect, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  ClipboardList,
  Edit,
  ExternalLink,
  FileCheck,
  History,
  Info,
  LayoutGrid,
  Loader2,
  RefreshCw,
  Table2,
  X,
  XCircle,
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { api } from '../../services/api';
import type {
  CertificationCheckRef,
  CertificationDetail,
  CertificationRun,
  CertificationTableOutcome,
  DqFailedRule,
} from '../../services/api';
import { Button } from '../ui/button';
import { SidePanel } from '../discover/SidePanel';
import { CertificationChecklist } from './CertificationChecklist';
import type { ChecklistRuleRow } from './CertificationChecklist';
import { catalogExplorerUrl } from '../../lib/databricksLinks';
import { useBrandingStore } from '../../stores/brandingStore';
import { formatPacific, parseUtc } from '../../lib/certificationDates';

export type DrawerTab = 'overview' | 'tables' | 'checklist' | 'history';

interface DatasetCertificationDrawerProps {
  datasetId: string;
  initialTab?: DrawerTab;
  /** Bump to refetch after an action on the page (sync, check, save). */
  reloadToken?: number;
  onClose: () => void;
  onEditContract: () => void;
  onSync: () => void;
  onCheckPolicy: () => void;
  isSyncing?: boolean;
  isChecking?: boolean;
}

const relative = (value: string | null | undefined) =>
  value ? formatDistanceToNow(parseUtc(value), { addSuffix: true }) : null;

// Rego messages end with "... table 'cat.sch.tbl'." Inside a table's own card
// that name is redundant, so read it as "this table".
function scopeMessage(message: string, tableName: string): string {
  const escaped = tableName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return message.replace(new RegExp(`\\b(table|view) '${escaped}'`, 'g'), 'this $1');
}

function StatusBadge({ certified, scanned }: { certified: boolean; scanned: boolean }) {
  if (certified) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800">
        <CheckCircle2 className="w-3 h-3 mr-1" /> Certified
      </span>
    );
  }
  if (!scanned) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-yellow-100 text-yellow-800">
        <Info className="w-3 h-3 mr-1" /> Awaiting Scan
      </span>
    );
  }
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-800">
      Uncertified
    </span>
  );
}

function TableStatusIcon({ status, className = 'w-4 h-4' }: { status: CertificationTableOutcome['status']; className?: string }) {
  if (status === 'pass') return <CheckCircle2 className={`${className} text-green-600`} aria-label="Passing" />;
  if (status === 'fail') return <XCircle className={`${className} text-red-600`} aria-label="Failing" />;
  return <CircleDashed className={`${className} text-gray-400`} aria-label="Not scanned yet" />;
}

function StatTile({ label, value, tone, sub }: { label: string; value: string; tone: 'good' | 'bad' | 'neutral'; sub?: string }) {
  const toneClass = tone === 'good' ? 'text-green-700' : tone === 'bad' ? 'text-red-600' : 'text-gray-900';
  return (
    <div className="bg-white rounded-lg border border-gray-200 px-4 py-3">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      <div className={`text-xl font-semibold mt-0.5 ${toneClass}`}>{value}</div>
      {sub && <div className="text-[11px] text-gray-400 mt-0.5">{sub}</div>}
    </div>
  );
}

function DqRulesTable({ rules, showTable }: { rules: DqFailedRule[]; showTable?: boolean }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200">
      <table className="w-full text-sm bg-white">
        <thead className="bg-gray-50 text-gray-500 text-xs border-b border-gray-200">
          <tr>
            <th className="text-left font-medium p-2 pl-3">Rule</th>
            <th className="text-left font-medium p-2">Column</th>
            <th className="text-right font-medium p-2">Score</th>
            <th className="text-right font-medium p-2">Threshold</th>
            <th className="text-right font-medium p-2 pr-3">Rows Failed</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rules.map((fr, i) => (
            <tr key={i} className="align-top">
              <td className="p-2 pl-3">
                <div className="font-medium text-gray-900">{fr.rule || 'Unnamed rule'}</div>
                <div className="text-[11px] text-gray-500">{[fr.dimension, fr.rule_type].filter(Boolean).join(' · ')}</div>
                {showTable && fr.table && <div className="text-[10px] text-gray-400 font-mono break-all">{fr.table}</div>}
              </td>
              <td className="p-2 text-gray-700">{fr.column || '—'}</td>
              <td className="p-2 text-right font-semibold text-red-600">{fr.score != null ? `${Number(fr.score).toFixed(2)}%` : '—'}</td>
              <td className="p-2 text-right text-gray-600">{fr.threshold != null ? `${Number(fr.threshold).toFixed(2)}%` : '—'}</td>
              <td className="p-2 pr-3 text-right text-gray-600">{fr.rows_failed != null ? Number(fr.rows_failed).toLocaleString() : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CheckList({ checks, scopeTo }: { checks: CertificationCheckRef[]; scopeTo?: string }) {
  return (
    <ul className="space-y-2">
      {checks.map((c, i) => (
        <li key={`${c.id}-${i}`} className="rounded-lg border border-red-100 bg-red-50/50 p-3">
          <div className="flex items-start gap-2">
            <XCircle className="w-4 h-4 text-red-600 shrink-0 mt-0.5" />
            <div className="min-w-0">
              <div className="text-sm font-medium text-gray-900">{c.description || c.id}</div>
              <div className="text-[11px] uppercase tracking-wide text-gray-500 mt-0.5">{c.category}</div>
              {c.messages.length > 0 && (
                <ul className="mt-1.5 space-y-0.5 text-xs text-gray-700">
                  {c.messages.map((m, mi) => (
                    <li key={mi}>• {scopeTo ? scopeMessage(m, scopeTo) : m}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

function OverviewTab({ detail, onOpenTable }: { detail: CertificationDetail; onOpenTable: (name: string) => void }) {
  const ov = detail.overview;
  const about: [string, string | null][] = [
    ['Data product', ov.data_product],
    ['Domain', ov.domain],
    ['Contract status', ov.status],
    ['Contract version', ov.odcs_version],
  ];
  return (
    <div className="space-y-5">
      {detail.categories.length > 0 && (
        <section>
          <h4 className="text-sm font-semibold text-gray-900 mb-2">Certification by category</h4>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
            {detail.categories.map((c) => (
              <div
                key={c.category}
                className={`rounded-lg border px-3 py-2 ${c.status === 'pass' ? 'border-green-200 bg-green-50' : 'border-red-200 bg-red-50'}`}
              >
                <div className="flex items-center gap-1.5 text-sm font-medium text-gray-900">
                  {c.status === 'pass' ? <CheckCircle2 className="w-4 h-4 text-green-600" /> : <XCircle className="w-4 h-4 text-red-600" />}
                  {c.category}
                </div>
                <div className={`text-xs mt-0.5 ${c.status === 'pass' ? 'text-green-700' : 'text-red-700'}`}>
                  {c.passed} of {c.passed + c.failed} passed
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h4 className="text-sm font-semibold text-gray-900 mb-2">Tables</h4>
        {detail.tables.length === 0 ? (
          <p className="text-sm text-gray-500">The contract declares no tables.</p>
        ) : (
          <div className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
            {detail.tables.map((t) => {
              const issues = t.failed_checks.reduce((n, c) => n + Math.max(c.messages.length, 1), 0) + t.dq_failed_rules.length;
              return (
                <button
                  key={t.name}
                  onClick={() => onOpenTable(t.name)}
                  className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-gray-50 transition-colors group"
                >
                  <TableStatusIcon status={t.status} />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-gray-900 truncate">{t.table}</div>
                    <div className="text-[11px] font-mono text-gray-500 truncate">{t.name}</div>
                  </div>
                  <span className={`text-xs whitespace-nowrap ${t.status === 'fail' ? 'text-red-600 font-medium' : 'text-gray-500'}`}>
                    {t.status === 'fail' ? `${issues} issue${issues === 1 ? '' : 's'}` : t.status === 'pass' ? 'All checks pass' : 'Not scanned yet'}
                  </span>
                  <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-gray-500" />
                </button>
              );
            })}
          </div>
        )}
      </section>

      {detail.dataset_checks.length > 0 && (
        <section>
          <h4 className="text-sm font-semibold text-gray-900 mb-2">Data-set-level failures</h4>
          <CheckList checks={detail.dataset_checks} />
        </section>
      )}

      <section className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
        <h4 className="text-sm font-semibold text-gray-900">About this data set</h4>
        {ov.parse_error ? (
          <p className="text-sm text-red-700">The contract YAML couldn't be parsed: {ov.parse_error}</p>
        ) : (
          <>
            {ov.purpose && <p className="text-sm text-gray-700 leading-relaxed">{ov.purpose}</p>}
            <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {about.map(([label, value]) => (
                <div key={label}>
                  <dt className="text-[11px] uppercase tracking-wide text-gray-500">{label}</dt>
                  <dd className="text-sm text-gray-900 mt-0.5">{value || '—'}</dd>
                </div>
              ))}
            </dl>
            {ov.usage && (
              <div>
                <div className="text-[11px] uppercase tracking-wide text-gray-500">Usage</div>
                <p className="text-sm text-gray-700 mt-0.5">{ov.usage}</p>
              </div>
            )}
            {ov.limitations && (
              <div>
                <div className="text-[11px] uppercase tracking-wide text-gray-500">Limitations</div>
                <p className="text-sm text-gray-700 mt-0.5">{ov.limitations}</p>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

type TableFilter = 'all' | 'fail' | 'pass';

function TablesTab({
  detail,
  expanded,
  onToggle,
}: {
  detail: CertificationDetail;
  expanded: Set<string>;
  onToggle: (name: string) => void;
}) {
  const [filter, setFilter] = useState<TableFilter>('all');
  const workspaceUrl = useBrandingStore((s) => s.databricksWorkspaceUrl);
  const failing = detail.tables.filter((t) => t.status === 'fail').length;
  const passing = detail.tables.filter((t) => t.status === 'pass').length;
  const shown = detail.tables.filter((t) => filter === 'all' || t.status === filter);
  const hasSnapshot = detail.tables.some((t) => t.type !== null);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        {(
          [
            { id: 'all', label: `All (${detail.tables.length})` },
            { id: 'fail', label: `Failing (${failing})` },
            { id: 'pass', label: `Passing (${passing})` },
          ] as { id: TableFilter; label: string }[]
        ).map((opt) => (
          <button
            key={opt.id}
            onClick={() => setFilter(opt.id)}
            className={`px-3 py-1 text-sm font-medium rounded-md transition-colors ${
              filter === opt.id ? 'bg-white text-gray-900 shadow-sm ring-1 ring-gray-200' : 'text-gray-500 hover:text-gray-900 hover:bg-gray-100'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {shown.length === 0 && <p className="text-sm text-gray-500 py-6 text-center">No tables match this filter.</p>}

      {shown.map((t) => {
        const open = expanded.has(t.name);
        const explorer = catalogExplorerUrl(workspaceUrl, t.catalog, t.schema_name, t.table);
        const tags = Object.entries(t.tags || {});
        const hasIssues = t.failed_checks.length > 0 || t.dq_failed_rules.length > 0;
        return (
          <div
            key={t.name}
            id={`cert-table-${t.name}`}
            className={`bg-white rounded-lg border ${t.status === 'fail' ? 'border-red-200' : t.status === 'pass' ? 'border-green-200' : 'border-gray-200'}`}
          >
            <div className="flex items-center gap-3 px-3 py-2.5">
              <button onClick={() => onToggle(t.name)} className="flex items-center gap-3 min-w-0 flex-1 text-left" aria-expanded={open}>
                {open ? <ChevronDown className="w-4 h-4 text-gray-400 shrink-0" /> : <ChevronRight className="w-4 h-4 text-gray-400 shrink-0" />}
                <TableStatusIcon status={t.status} />
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-900 truncate">{t.table}</span>
                    {t.type && (
                      <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">{t.type}</span>
                    )}
                    {t.certified && (
                      <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-green-100 text-green-700">UC certified</span>
                    )}
                    {t.exists === false && (
                      <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-red-100 text-red-700">Not found</span>
                    )}
                  </div>
                  <div className="text-[11px] font-mono text-gray-500 truncate">{t.name}</div>
                </div>
              </button>
              {explorer && (
                <a
                  href={explorer}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-gray-500 hover:text-primary inline-flex items-center gap-1 shrink-0"
                  title="Open in Catalog Explorer"
                >
                  <ExternalLink className="w-3.5 h-3.5" /> Catalog
                </a>
              )}
            </div>

            {open && (
              <div className="border-t border-gray-100 px-4 py-3 space-y-4">
                {t.status === 'not_scanned' && (
                  <p className="text-sm text-gray-500">This table hasn't been evaluated yet. Run a policy check to see its results.</p>
                )}
                {t.status === 'pass' && !hasIssues && (
                  <p className="text-sm text-green-700 flex items-center gap-1.5">
                    <CheckCircle2 className="w-4 h-4" /> Every certification check passes for this table.
                  </p>
                )}
                {t.failed_checks.length > 0 && <CheckList checks={t.failed_checks} scopeTo={t.name} />}
                {t.dq_failed_rules.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold text-gray-700 mb-1.5">Failing data quality rules</div>
                    <DqRulesTable rules={t.dq_failed_rules} />
                  </div>
                )}
                {tags.length > 0 ? (
                  <div>
                    <div className="text-xs font-semibold text-gray-700 mb-1.5">Tags</div>
                    <div className="flex flex-wrap gap-1">
                      {tags.map(([k, v]) => (
                        <span key={k} className="text-[11px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-700 border border-gray-200">
                          {k}: {String(v)}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : (
                  !hasSnapshot &&
                  t.status !== 'not_scanned' && (
                    <p className="text-[11px] text-gray-400">Table type and tags appear after the next policy run.</p>
                  )
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ChecklistTab({ detail }: { detail: CertificationDetail }) {
  const ruleRows: ChecklistRuleRow[] = detail.rule_results.map((r) => ({
    ...r,
    resource_type: 'data_product',
    resource_id: detail.dataset_id,
    resource: { name: detail.name },
    policy: 'data_certification',
    severity: r.passed ? 'NONE' : 'HIGH',
  }));
  const dqRules = detail.data_quality.failed_rules || [];
  const legacy = detail.certification_violations || [];
  const hasFailures = ruleRows.some((r) => !r.passed) || dqRules.length > 0 || legacy.length > 0;

  return (
    <div className="space-y-5">
      <div className="bg-white rounded-lg border border-gray-200 overflow-x-auto">
        {ruleRows.length > 0 ? (
          <CertificationChecklist ruleRows={ruleRows} />
        ) : legacy.length > 0 ? (
          <div className="p-4">
            <p className="text-sm text-gray-600 mb-3">This data set fails the following checks required for certification:</p>
            <ul className="space-y-2">
              {legacy.map((v, i) => (
                <li key={i} className="flex items-start gap-3 text-sm text-gray-800">
                  <span className="flex-shrink-0 w-6 h-6 rounded-full bg-red-100 text-red-700 flex items-center justify-center text-xs font-bold">{i + 1}</span>
                  <span className="pt-0.5">{v.replace(/^\d+\.\s*/, '')}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <CertificationChecklist ruleRows={[]} />
        )}
      </div>
      {dqRules.length > 0 && (
        <section>
          <h4 className="text-sm font-semibold text-gray-900 mb-2">Failing data quality rules within the reliability window</h4>
          <DqRulesTable rules={dqRules} showTable />
        </section>
      )}
      {hasFailures && (
        <div className="p-4 bg-blue-50 text-blue-800 rounded-lg border border-blue-100 text-sm">
          <strong>Next steps:</strong> Once the data engineering team resolves these issues in Databricks (e.g., by adding missing
          tags, defining RBAC, or improving data quality scores), the next Enforcement Sentinel run will automatically detect the
          changes and generate a Data Certification request.
        </div>
      )}
    </div>
  );
}

function RunStrip({
  runs,
  selected,
  onSelect,
}: {
  runs: CertificationRun[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  // Oldest → newest, left to right.
  const ordered = [...runs].reverse();
  return (
    <div className="flex flex-wrap gap-[3px]" role="list" aria-label="Recent policy runs">
      {ordered.map((r) => (
        <button
          key={r.request_id}
          role="listitem"
          onClick={() => onSelect(r.request_id)}
          title={`${r.run_at ? formatPacific(r.run_at) : 'Unknown time'} — ${r.passed ? 'all checks passed' : `${r.failed_count} failing check${r.failed_count === 1 ? '' : 's'}`}`}
          className={`w-3 h-6 rounded-sm transition-transform hover:scale-y-110 ${r.passed ? 'bg-green-500' : 'bg-red-500'} ${
            selected === r.request_id ? 'ring-2 ring-offset-1 ring-gray-900' : ''
          }`}
        />
      ))}
    </div>
  );
}

function HistoryTab({ detail, onEditContract }: { detail: CertificationDetail; onEditContract: () => void }) {
  const { runs, changes, contract_versions } = detail.history;
  const [selectedRun, setSelectedRun] = useState<string | null>(runs[0]?.request_id ?? null);
  const run = runs.find((r) => r.request_id === selectedRun) ?? null;
  const passedRuns = runs.filter((r) => r.passed).length;
  const oldest = runs[runs.length - 1];

  return (
    <div className="space-y-5">
      <section className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex items-baseline justify-between gap-3 mb-3">
          <h4 className="text-sm font-semibold text-gray-900">Recent policy runs</h4>
          {runs.length > 0 && (
            <span className="text-xs text-gray-500">
              Passed {passedRuns} of {runs.length} run{runs.length === 1 ? '' : 's'}
              {oldest?.run_at ? ` since ${formatPacific(oldest.run_at)}` : ''}
            </span>
          )}
        </div>
        {runs.length === 0 ? (
          <p className="text-sm text-gray-500">No policy runs recorded for this data set yet.</p>
        ) : (
          <>
            <RunStrip runs={runs} selected={selectedRun} onSelect={setSelectedRun} />
            <div className="flex items-center gap-4 mt-2 text-[11px] text-gray-500">
              <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-green-500" /> Passed</span>
              <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-red-500" /> Failed</span>
              <span className="ml-auto">Oldest → newest · click a run for details</span>
            </div>
            {run && (
              <div className="mt-3 rounded-md bg-gray-50 border border-gray-100 p-3 text-sm">
                <div className="font-medium text-gray-900">
                  {run.run_at ? formatPacific(run.run_at) : 'Unknown time'} —{' '}
                  {run.passed ? (
                    <span className="text-green-700">all {run.total_count} checks passed</span>
                  ) : (
                    <span className="text-red-600">
                      {run.failed_count} of {run.total_count} checks failing
                    </span>
                  )}
                </div>
                {run.failed_checks.length > 0 && (
                  <ul className="mt-1.5 space-y-0.5 text-xs text-gray-700">
                    {run.failed_checks.map((c) => (
                      <li key={c.id} className="flex items-center gap-1.5">
                        <XCircle className="w-3 h-3 text-red-500" /> {c.description || c.id}
                        <span className="text-gray-400">· {c.category}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </>
        )}
      </section>

      {changes.length > 0 && (
        <section className="bg-white rounded-lg border border-gray-200 p-4">
          <h4 className="text-sm font-semibold text-gray-900 mb-3">What changed</h4>
          <ol className="relative border-l border-gray-200 ml-1.5 space-y-4">
            {changes.map((c) => (
              <li key={c.request_id} className="ml-4">
                <span
                  className={`absolute -left-[5px] mt-1.5 w-2.5 h-2.5 rounded-full ${c.failed_count === 0 ? 'bg-green-500' : 'bg-red-500'}`}
                />
                <div className="text-xs text-gray-500">
                  {c.run_at ? formatPacific(c.run_at) : 'Unknown time'}
                  {c.run_at && <span className="text-gray-400"> · {relative(c.run_at)}</span>}
                </div>
                {c.kind === 'first' ? (
                  <div className="text-sm text-gray-800 mt-0.5">
                    Earliest run shown — {c.failed_count === 0 ? 'all checks passing' : `${c.failed_count} failing check${c.failed_count === 1 ? '' : 's'}`}
                  </div>
                ) : (
                  <div className="text-sm text-gray-800 mt-0.5">
                    {c.failed_count === 0 ? 'All checks passing' : `${c.failed_count} failing check${c.failed_count === 1 ? '' : 's'}`}
                  </div>
                )}
                <ul className="mt-1 space-y-0.5 text-xs">
                  {c.newly_failing.map((x) => (
                    <li key={`n-${x.id}`} className="flex items-center gap-1.5 text-red-700">
                      <XCircle className="w-3 h-3" /> {c.kind === 'first' ? '' : 'Started failing: '}
                      {x.description || x.id}
                    </li>
                  ))}
                  {c.resolved.map((x) => (
                    <li key={`r-${x.id}`} className="flex items-center gap-1.5 text-green-700">
                      <CheckCircle2 className="w-3 h-3" /> Fixed: {x.description || x.id}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </section>
      )}

      <section className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-sm font-semibold text-gray-900">Contract versions</h4>
          <Button variant="outline" size="sm" onClick={onEditContract} className="text-xs h-7 px-2 gap-1">
            <Edit className="w-3.5 h-3.5" /> Open editor
          </Button>
        </div>
        {contract_versions.length === 0 ? (
          <p className="text-sm text-gray-500">No contract versions saved.</p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {contract_versions.map((v) => (
              <li key={v.version} className="flex items-center gap-3 py-2 text-sm">
                <span className="font-semibold text-gray-900 w-10">v{v.version}</span>
                <span className="text-gray-600">{v.created_at ? formatPacific(v.created_at) : '—'}</span>
                <span className="text-gray-400 text-xs">{v.created_by || 'Contract sync'}</span>
                {v.is_active && (
                  <span className="ml-auto px-2 py-0.5 bg-green-100 text-green-800 text-[10px] rounded-full font-bold">ACTIVE</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Drawer
// ---------------------------------------------------------------------------

/**
 * Per-data-set detail for the Data Certification page: overview, red/green
 * tables, the full checklist, and run + contract history. Opened by clicking a
 * data set's name (or deep-linked via ``?dataset=<id>``).
 */
export function DatasetCertificationDrawer({
  datasetId,
  initialTab = 'overview',
  reloadToken = 0,
  onClose,
  onEditContract,
  onSync,
  onCheckPolicy,
  isSyncing,
  isChecking,
}: DatasetCertificationDrawerProps) {
  const [tab, setTab] = useState<DrawerTab>(initialTab);
  const [loaded, setLoaded] = useState<{ key: string; detail: CertificationDetail | null; error: string | null } | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const loadKey = `${datasetId}:${reloadToken}`;
  useEffect(() => {
    let cancelled = false;
    api
      .getCertificationDetail(datasetId)
      .then((detail) => !cancelled && setLoaded({ key: loadKey, detail, error: null }))
      .catch((e: Error) => !cancelled && setLoaded({ key: loadKey, detail: null, error: e.message }));
    return () => {
      cancelled = true;
    };
  }, [datasetId, loadKey]);

  // Keep showing the previous result while a refresh is in flight.
  const detail = loaded?.detail ?? null;
  const error = loaded?.error ?? null;
  const refreshing = loaded !== null && loaded.key !== loadKey;

  const openTable = (name: string) => {
    setExpanded((prev) => new Set(prev).add(name));
    setTab('tables');
    requestAnimationFrame(() => document.getElementById(`cert-table-${name}`)?.scrollIntoView({ block: 'nearest' }));
  };
  const toggleTable = (name: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const scanned = !!detail && detail.rule_results.length > 0;
  const failedChecks = detail ? detail.rule_results.filter((r) => !r.passed).length : 0;
  const passingTables = detail ? detail.tables.filter((t) => t.status === 'pass').length : 0;
  const dqCount = detail?.data_quality.failed_rule_count;

  const tabs: { id: DrawerTab; label: string; icon: typeof LayoutGrid; count?: number }[] = [
    { id: 'overview', label: 'Overview', icon: LayoutGrid },
    { id: 'tables', label: 'Tables', icon: Table2, count: detail?.tables.length },
    { id: 'checklist', label: 'Checklist', icon: ClipboardList, count: detail?.rule_results.length },
    { id: 'history', label: 'History', icon: History },
  ];

  return (
    <SidePanel label={`${datasetId} certification details`} onClose={onClose}>
      <div className="space-y-4">
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <FileCheck className="w-5 h-5 text-gray-700 shrink-0" />
                <h3 className="text-lg font-semibold text-gray-900 truncate">{detail?.name || datasetId}</h3>
                {detail && <StatusBadge certified={detail.certified} scanned={scanned} />}
                {refreshing && <Loader2 className="w-4 h-4 animate-spin text-gray-400" />}
              </div>
              {detail && (
                <p className="text-xs text-gray-500 mt-1">
                  {[
                    detail.overview.data_product,
                    detail.overview.domain && `Domain: ${detail.overview.domain}`,
                    detail.contract.version != null && `Contract v${detail.contract.version}`,
                    detail.last_policy_run && `Last policy run ${relative(detail.last_policy_run)}`,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </p>
              )}
            </div>
            <Button variant="ghost" size="sm" onClick={onClose} className="w-8 h-8 p-0 shrink-0" aria-label="Close">
              <X className="w-4 h-4" />
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-2 mt-3">
            <Button
              variant="outline"
              size="sm"
              onClick={onCheckPolicy}
              disabled={isChecking}
              className="text-xs h-7 px-2 gap-1 border-purple-200 text-purple-600 hover:bg-purple-50"
            >
              {isChecking ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileCheck className="w-3.5 h-3.5" />}
              Run policy check
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onSync}
              disabled={isSyncing}
              className="text-xs h-7 px-2 gap-1 border-green-200 text-green-600 hover:bg-green-50"
            >
              {isSyncing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              Sync contract
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onEditContract}
              className="text-xs h-7 px-2 gap-1 border-blue-200 text-blue-600 hover:bg-blue-50"
            >
              <Edit className="w-3.5 h-3.5" /> Edit contract
            </Button>
          </div>
        </div>

        {!loaded ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-500">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading data set…
          </div>
        ) : error && !detail ? (
          <div className="p-4 rounded-lg bg-red-50 text-red-700 border border-red-200 text-sm flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" /> {error}
          </div>
        ) : detail ? (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
              <StatTile
                label="Tables passing"
                value={detail.tables.length ? `${passingTables} / ${detail.tables.length}` : '—'}
                tone={!scanned || !detail.tables.length ? 'neutral' : passingTables === detail.tables.length ? 'good' : 'bad'}
              />
              <StatTile
                label="Failed checks"
                value={scanned ? String(failedChecks) : '—'}
                tone={!scanned ? 'neutral' : failedChecks === 0 ? 'good' : 'bad'}
                sub={scanned ? `of ${detail.rule_results.length} checks` : 'Not scanned yet'}
              />
              <StatTile
                label="Failed DQ rules"
                value={dqCount == null || dqCount < 0 ? 'n/a' : String(dqCount)}
                tone={dqCount == null || dqCount < 0 ? 'neutral' : dqCount === 0 ? 'good' : 'bad'}
                sub={dqCount != null && dqCount < 0 ? "History couldn't be fetched" : 'Within reliability window'}
              />
              <StatTile
                label="Last policy run"
                value={detail.last_policy_run ? relative(detail.last_policy_run)! : 'Never'}
                tone="neutral"
                sub={detail.last_policy_run ? formatPacific(detail.last_policy_run) : undefined}
              />
            </div>

            <div className="flex items-center gap-1 border-b border-gray-200 overflow-x-auto" role="tablist">
              {tabs.map(({ id, label, icon: Icon, count }) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={tab === id}
                  onClick={() => setTab(id)}
                  className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${
                    tab === id ? 'border-primary text-gray-900' : 'border-transparent text-gray-500 hover:text-gray-900'
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  {label}
                  {count !== undefined && count > 0 && <span className="text-xs text-gray-400">{count}</span>}
                </button>
              ))}
            </div>

            {tab === 'overview' && <OverviewTab detail={detail} onOpenTable={openTable} />}
            {tab === 'tables' && <TablesTab detail={detail} expanded={expanded} onToggle={toggleTable} />}
            {tab === 'checklist' && <ChecklistTab detail={detail} />}
            {tab === 'history' && <HistoryTab detail={detail} onEditContract={onEditContract} />}
          </>
        ) : null}
      </div>
    </SidePanel>
  );
}

export default DatasetCertificationDrawer;
