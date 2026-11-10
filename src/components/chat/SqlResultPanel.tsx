/**
 * What a `run_sql` call returned, and how: the rows (unless they're already
 * charted), then "How this was answered" — the tables the query read with
 * their certification, data quality, owner and source system, and the SQL
 * itself with links to re-run it in Databricks.
 *
 * People asked "what table is this from?" and "can I trust it?" after the
 * assistant answered from a query; this answers both without another turn.
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
    AlertTriangle,
    Check,
    ChevronDown,
    ChevronRight,
    Copy,
    Database,
    ExternalLink,
    ShieldCheck,
} from 'lucide-react';
import { useBrandingStore } from '../../stores/brandingStore';
import { queryHistoryUrl, sqlEditorUrl } from '../../lib/databricksLinks';
import type { SqlResult, SqlSourceTable } from './sqlResult';

const PREVIEW_ROWS = 10;

function cell(value: unknown): string {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
}

/** "Reliability 82% · 1 failed check", from whatever the quality summary holds. */
function qualitySummary(dq: Record<string, unknown> | null | undefined): { text: string; failing: boolean } | null {
    if (!dq) return null;
    const parts: string[] = [];
    const reliability = dq.reliability;
    if (typeof reliability === 'number') {
        parts.push(`Reliability ${reliability <= 1 ? Math.round(reliability * 100) : Math.round(reliability)}%`);
    } else if (typeof reliability === 'string' && reliability) {
        parts.push(`Reliability ${reliability}`);
    }
    const failed = typeof dq.failed_rule_count === 'number' ? dq.failed_rule_count : 0;
    if (failed > 0) parts.push(`${failed} failed check${failed === 1 ? '' : 's'}`);
    if (!parts.length) return null;
    return { text: parts.join(' · '), failing: failed > 0 };
}

function SourceRow({ source }: { source: SqlSourceTable }) {
    if (!source.in_catalog) {
        return (
            <li className="text-[11px] text-gray-600">
                <span className="font-mono text-gray-800">{source.name}</span>
                <span className="text-gray-400"> · not in the catalog, so no certification or quality details</span>
            </li>
        );
    }
    const dq = qualitySummary(source.data_quality);
    const synced = source.catalog_synced_at ? new Date(source.catalog_synced_at).toLocaleDateString() : null;
    return (
        <li className="text-[11px] text-gray-600 space-y-0.5">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                {source.discover_path ? (
                    <Link to={source.discover_path} className="font-mono text-primary hover:underline">
                        {source.name}
                    </Link>
                ) : (
                    <span className="font-mono text-gray-800">{source.name}</span>
                )}
                {source.certified ? (
                    <span className="inline-flex items-center gap-0.5 text-emerald-700">
                        <ShieldCheck className="h-3 w-3" /> Certified
                    </span>
                ) : (
                    <span className="inline-flex items-center gap-0.5 text-amber-700">
                        <AlertTriangle className="h-3 w-3" /> Not certified
                    </span>
                )}
                {dq && (
                    <span className={dq.failing ? 'text-amber-700' : 'text-gray-600'}>{dq.text}</span>
                )}
            </div>
            <div className="text-gray-500">
                {[
                    source.source_system ? `Source system: ${source.source_system}` : null,
                    source.owner ? `Owner: ${source.owner}` : null,
                    synced ? `Catalog details as of ${synced}` : null,
                ]
                    .filter(Boolean)
                    .join(' · ')}
            </div>
            {!source.certified && source.certification_issues && source.certification_issues.length > 0 && (
                <div className="text-gray-500">Not certified because: {source.certification_issues.join('; ')}</div>
            )}
        </li>
    );
}

export function SqlResultPanel({ result, showRows }: { result: SqlResult; showRows: boolean }) {
    const workspaceUrl = useBrandingStore((s) => s.databricksWorkspaceUrl);
    const [open, setOpen] = useState(false);
    const [copied, setCopied] = useState(false);

    const columns = result.columns ?? [];
    const rows = result.rows ?? [];
    const total = result.row_count ?? rows.length;
    const singleValue = rows.length <= 1 && columns.length <= 1;
    const sources = result.source_tables ?? [];
    const editor = sqlEditorUrl(workspaceUrl);
    const history = queryHistoryUrl(workspaceUrl, result.statement_id);
    const uncertified = sources.some((s) => s.in_catalog && !s.certified);

    const copySql = async () => {
        try {
            await navigator.clipboard.writeText(result.sql ?? '');
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked */
        }
    };

    return (
        <div className="mt-1 w-full max-w-[80%] space-y-1">
            {showRows && !singleValue && rows.length > 0 && (
                <div className="rounded-md border border-gray-200 overflow-auto max-h-72">
                    <table className="min-w-full text-[11px]">
                        <thead className="bg-gray-50 sticky top-0">
                            <tr>
                                {columns.map((c) => (
                                    <th key={c} className="px-2 py-1 text-left font-semibold text-gray-600 whitespace-nowrap">
                                        {c}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {rows.slice(0, PREVIEW_ROWS).map((row, i) => (
                                <tr key={i} className="border-t border-gray-100">
                                    {row.map((v, j) => (
                                        <td key={j} className="px-2 py-1 text-gray-800 whitespace-nowrap">
                                            {cell(v)}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {(total > PREVIEW_ROWS || result.truncated) && (
                        <div className="px-2 py-1 text-[10px] text-gray-500 bg-gray-50 border-t border-gray-100">
                            Showing {Math.min(PREVIEW_ROWS, rows.length)} of {total.toLocaleString()}
                            {result.truncated ? '+' : ''} rows.
                        </div>
                    )}
                </div>
            )}

            <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                className="inline-flex items-center gap-1 text-[11px] font-medium text-gray-500 hover:text-gray-700 transition-colors"
                aria-expanded={open}
            >
                {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                How this was answered
                <span className="text-gray-400 font-normal">
                    {sources.length > 0
                        ? `(from ${sources.length === 1 ? sources[0].name : `${sources.length} tables`}${uncertified ? ' · not all certified' : ''})`
                        : '(SQL)'}
                </span>
            </button>
            {open && (
                <div className="rounded-md border border-gray-200 bg-gray-50/80 px-3 py-2 space-y-2">
                    {sources.length > 0 && (
                        <div>
                            <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1 flex items-center gap-1">
                                <Database className="h-3 w-3" /> Data from
                            </div>
                            <ul className="space-y-1.5">
                                {sources.map((s) => (
                                    <SourceRow key={s.name} source={s} />
                                ))}
                            </ul>
                        </div>
                    )}
                    <div>
                        <div className="flex items-center justify-between mb-1">
                            <span className="text-[10px] uppercase tracking-wide text-gray-400">SQL, run with your permissions</span>
                            <button
                                type="button"
                                onClick={copySql}
                                className="inline-flex items-center gap-1 text-[11px] text-gray-500 hover:text-gray-700 transition-colors"
                            >
                                {copied ? <><Check className="h-3 w-3" /> Copied</> : <><Copy className="h-3 w-3" /> Copy SQL</>}
                            </button>
                        </div>
                        <pre className="text-[11px] font-mono text-gray-800 whitespace-pre-wrap break-all max-h-48 overflow-auto bg-white border border-gray-200 rounded px-2 py-1.5">
                            {result.sql}
                        </pre>
                    </div>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
                        {editor && (
                            <a href={editor} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
                                Open the SQL editor <ExternalLink className="h-3 w-3" />
                            </a>
                        )}
                        {history && (
                            <a href={history} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
                                Query history <ExternalLink className="h-3 w-3" />
                            </a>
                        )}
                        {result.executed_at && (
                            <span className="text-gray-400">Ran {new Date(result.executed_at).toLocaleString()}</span>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
