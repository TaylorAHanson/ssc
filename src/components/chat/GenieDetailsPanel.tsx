/**
 * Expandable details panel rendered below a completed Genie tool pill.
 *
 * The LLM normally rephrases Genie's answer for the user, which is
 * great for conversational flow but loses fidelity (the SQL Genie
 * generated, the actual rows, any chart, the deep link back to the
 * conversation in Databricks). This panel surfaces all of that
 * verbatim — collapsed by default to keep the chat compact.
 *
 * The panel relies on ``parseGenieResult`` to flatten Genie's varied
 * payload shapes into a stable ``ParsedGenieResult`` so this component
 * stays presentational. When the parser can't pull anything
 * structured (Genie's MCP server occasionally returns a
 * narrative-only payload), the panel falls back to a "Raw response"
 * inspector instead of looking empty — the user is still seeing the
 * full fidelity of what came back.
 */
import { useMemo, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Copy, ExternalLink } from 'lucide-react';

import { VegaLiteChart } from './VegaLiteChart';
import { parseGenieResult, type ParsedGenieResult } from './parseGenieResult';

export interface GenieDetailsPanelProps {
    /** Raw payload from the Genie poll endpoint's ``result`` field. */
    result: Record<string, unknown> | null | undefined;
}

export function GenieDetailsPanel({ result }: GenieDetailsPanelProps) {
    // Stable parsing — cheap, but pin it on the input identity so the
    // collapse state below doesn't blink when React rerenders.
    const parsed: ParsedGenieResult = useMemo(() => parseGenieResult(result), [result]);

    // What we have to show drives both the open default and the
    // header chip summary.
    const hasStructured = !!(parsed.sql || parsed.preview || parsed.chartSpec || parsed.narrative);
    const hasRawPayload = !!result && Object.keys(result).length > 0;
    const hasAnything = hasStructured || hasRawPayload || parsed.deepLink;
    if (!hasAnything) return null;

    // Default to expanded when there's content worth seeing. The user
    // can collapse for a compact view but a collapsed-empty-looking
    // panel was confusing ("the box is empty") so we lead with content.
    const [open, setOpen] = useState(hasStructured);
    const [showRaw, setShowRaw] = useState(false);
    const [copied, setCopied] = useState(false);

    const handleCopySql = async () => {
        if (!parsed.sql) return;
        try {
            await navigator.clipboard.writeText(parsed.sql);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked — silently no-op */
        }
    };

    // Build the small "what's inside" header chip. Falls back to
    // "raw response" when there's nothing structured so the panel
    // never reads as empty.
    const summaryBits = [
        parsed.sql ? 'SQL' : null,
        parsed.preview ? `${parsed.preview.rows.length} rows` : null,
        parsed.chartSpec ? 'chart' : null,
        !hasStructured && hasRawPayload ? 'raw response' : null,
    ].filter(Boolean);

    return (
        <div className="mt-2 border border-gray-200 bg-white rounded-lg overflow-hidden">
            <div className="flex items-center justify-between gap-2 px-3 py-2">
                <button
                    type="button"
                    onClick={() => setOpen((v) => !v)}
                    className="flex items-center gap-1.5 text-xs font-medium text-gray-700 hover:text-gray-900"
                    aria-expanded={open}
                >
                    {open ? (
                        <ChevronDown className="w-3.5 h-3.5" />
                    ) : (
                        <ChevronRight className="w-3.5 h-3.5" />
                    )}
                    <span>Genie details</span>
                    {summaryBits.length > 0 && (
                        <span className="text-[11px] font-normal text-gray-500">
                            {summaryBits.join(' · ')}
                        </span>
                    )}
                </button>
                {parsed.deepLink && (
                    <a
                        href={parsed.deepLink}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-xs font-medium text-brand-blue hover:text-brand-blue-dark"
                        title="Open this conversation in Databricks Genie"
                    >
                        Open in Genie
                        <ExternalLink className="w-3 h-3" />
                    </a>
                )}
                {!parsed.deepLink && parsed.authSource === 'sp' && (
                    // Local-dev hint: we ran under the service
                    // principal so the conversation isn't visible in
                    // the user's Databricks Genie chat history.
                    // Better to explain than show a broken link.
                    <span
                        className="text-[11px] text-gray-400 italic"
                        title="Local dev uses the service principal, so this conversation lives under the SP's identity rather than yours and can't be opened in your Databricks Genie chat history."
                    >
                        Local dev — link unavailable
                    </span>
                )}
            </div>

            {open && (
                <div className="border-t border-gray-100 px-3 py-3 space-y-3 bg-gray-50">
                    {parsed.narrative && (
                        <section>
                            <h5 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 mb-1">
                                Genie's answer
                            </h5>
                            <p className="text-sm text-gray-800 whitespace-pre-wrap">
                                {parsed.narrative}
                            </p>
                        </section>
                    )}

                    {parsed.sql && (
                        <section>
                            <div className="flex items-center justify-between mb-1">
                                <h5 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                                    SQL
                                </h5>
                                <button
                                    type="button"
                                    onClick={handleCopySql}
                                    className="inline-flex items-center gap-1 text-[11px] text-gray-500 hover:text-gray-700"
                                    title="Copy SQL"
                                >
                                    {copied ? (
                                        <>
                                            <Check className="w-3 h-3" /> Copied
                                        </>
                                    ) : (
                                        <>
                                            <Copy className="w-3 h-3" /> Copy
                                        </>
                                    )}
                                </button>
                            </div>
                            <pre className="text-xs bg-white border border-gray-200 rounded-md px-3 py-2 overflow-x-auto font-mono leading-relaxed text-gray-800">
                                {parsed.sql}
                            </pre>
                        </section>
                    )}

                    {parsed.preview && parsed.preview.columns.length > 0 && (
                        <section>
                            <div className="flex items-center justify-between mb-1">
                                <h5 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                                    Result preview
                                </h5>
                                <span className="text-[11px] text-gray-500">
                                    {parsed.preview.totalRows !== undefined
                                        ? `${parsed.preview.rows.length} of ${parsed.preview.totalRows} rows`
                                        : `${parsed.preview.rows.length} rows`}
                                </span>
                            </div>
                            <div className="overflow-x-auto bg-white border border-gray-200 rounded-md">
                                <table className="min-w-full text-xs">
                                    <thead className="bg-gray-100 sticky top-0">
                                        <tr>
                                            {parsed.preview.columns.map((c) => (
                                                <th
                                                    key={c}
                                                    className="text-left font-medium text-gray-600 px-2.5 py-1.5 border-b border-gray-200 whitespace-nowrap"
                                                >
                                                    {c}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {parsed.preview.rows.map((row, i) => (
                                            <tr
                                                key={i}
                                                className="odd:bg-white even:bg-gray-50 border-b border-gray-100 last:border-b-0"
                                            >
                                                {parsed.preview!.columns.map((_, j) => (
                                                    <td
                                                        key={j}
                                                        className="px-2.5 py-1.5 text-gray-800 whitespace-nowrap font-mono"
                                                    >
                                                        {_formatCell(row[j])}
                                                    </td>
                                                ))}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            {parsed.preview.truncated && parsed.deepLink && (
                                <p className="text-[11px] text-gray-500 mt-1">
                                    Showing first {parsed.preview.rows.length} rows.{' '}
                                    <a
                                        href={parsed.deepLink}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="text-brand-blue hover:underline"
                                    >
                                        View the full result in Databricks Genie
                                    </a>
                                    .
                                </p>
                            )}
                        </section>
                    )}

                    {parsed.chartSpec && (
                        <section>
                            <h5 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 mb-1">
                                Chart
                            </h5>
                            <div className="bg-white border border-gray-200 rounded-md p-2">
                                <VegaLiteChart
                                    spec={parsed.chartSpec}
                                    data={
                                        // If the chart spec didn't bake in
                                        // its own data, hand it the preview
                                        // rows reshaped as records so it has
                                        // something to render.
                                        parsed.preview && !parsed.chartSpec.data
                                            ? parsed.preview.rows.map((row) => {
                                                const rec: Record<string, unknown> = {};
                                                parsed.preview!.columns.forEach((c, i) => {
                                                    rec[c] = row[i];
                                                });
                                                return rec;
                                            })
                                            : undefined
                                    }
                                />
                            </div>
                        </section>
                    )}

                    {!hasStructured && hasRawPayload && (
                        // Fallback: nothing structured to display but the
                        // payload isn't empty. Show the JSON directly
                        // rather than leaving a blank panel — at least
                        // the analyst can see what Genie returned.
                        <section>
                            <p className="text-xs text-gray-600 mb-1">
                                Genie didn't return structured SQL/results for this turn.
                                The raw response is available below for inspection.
                            </p>
                        </section>
                    )}

                    {hasRawPayload && (
                        <section>
                            <button
                                type="button"
                                onClick={() => setShowRaw((v) => !v)}
                                className="flex items-center gap-1 text-[11px] font-medium text-gray-500 hover:text-gray-700"
                                aria-expanded={showRaw}
                            >
                                {showRaw ? (
                                    <ChevronDown className="w-3 h-3" />
                                ) : (
                                    <ChevronRight className="w-3 h-3" />
                                )}
                                Raw response
                            </button>
                            {showRaw && (
                                <pre className="mt-1 text-[11px] bg-white border border-gray-200 rounded-md px-3 py-2 overflow-x-auto font-mono leading-snug text-gray-700 max-h-72 overflow-y-auto">
                                    {JSON.stringify(result, null, 2)}
                                </pre>
                            )}
                        </section>
                    )}
                </div>
            )}
        </div>
    );
}

function _formatCell(v: unknown): string {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'number') return Number.isFinite(v) ? v.toLocaleString() : String(v);
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
}
