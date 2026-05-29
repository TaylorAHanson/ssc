/**
 * Best-effort parser for the Databricks Genie response payload.
 *
 * The Managed-MCP Genie server (and the underlying Genie REST API) is
 * still evolving. Different code paths and versions ship slightly
 * different field names — sometimes ``attachments``, sometimes a
 * top-level ``text``; the SQL might land under ``query.query`` or just
 * ``query``; tabular results sometimes embed as ``data_array``,
 * sometimes ``data_typed_array``, occasionally as
 * ``statement_response.result.data_array``.
 *
 * This module hides all that variance behind a single normalized
 * ``ParsedGenieResult`` shape so the UI code stays clean. When the
 * upstream schema settles we can simplify in one place.
 */

export interface GenieDataPreview {
    /** Column names in display order. */
    columns: string[];
    /** Rows of cells, parallel to ``columns``. May be a partial preview. */
    rows: Array<Array<unknown>>;
    /** Total row count if the upstream included one (often > rows.length). */
    totalRows?: number;
    /** True when the rows we're showing are a truncated preview. */
    truncated?: boolean;
}

export interface ParsedGenieResult {
    /**
     * Genie's verbatim natural-language narrative for the answer (the
     * one shown in the standalone Genie UI). May be empty when Genie
     * only returned tabular results.
     */
    narrative: string;
    /** SQL Genie generated to answer, if any. */
    sql?: string;
    /** Tabular preview of the result rows. */
    preview?: GenieDataPreview;
    /** Vega-Lite chart spec returned by Genie, if any. */
    chartSpec?: Record<string, unknown>;
    /**
     * Per-conversation deep link into the Databricks UI. Built by the
     * backend and surfaced as ``_deep_link`` on the result envelope.
     */
    deepLink?: string;
    /**
     * How the backend authenticated the call: ``'obo'`` means under
     * the user's own identity (deep links work, since the user owns
     * the conversation), ``'sp'`` means the local-dev service-
     * principal fallback was used (the user can't reach the
     * conversation in Databricks One — it's owned by the SP). The UI
     * uses this to hide the deep-link button and show a small local-
     * dev hint instead of a broken link.
     */
    authSource?: 'obo' | 'sp';
    /** Source conversation/message identifiers (debug + future linking). */
    conversationId?: string;
    messageId?: string;
}

/**
 * Walk the Genie response payload and pull out the bits the UI cares
 * about. Always returns a value — fields are absent when the upstream
 * didn't supply them, never throws.
 */
export function parseGenieResult(
    raw: Record<string, unknown> | null | undefined,
): ParsedGenieResult {
    if (!raw || typeof raw !== 'object') {
        return { narrative: '' };
    }

    const out: ParsedGenieResult = {
        narrative: '',
        deepLink: typeof raw._deep_link === 'string' ? (raw._deep_link as string) : undefined,
        authSource:
            raw._auth_source === 'obo' || raw._auth_source === 'sp'
                ? (raw._auth_source as 'obo' | 'sp')
                : undefined,
        conversationId:
            typeof raw.conversation_id === 'string' ? (raw.conversation_id as string) : undefined,
        messageId:
            typeof raw.message_id === 'string'
                ? (raw.message_id as string)
                : typeof raw.response_id === 'string'
                    ? (raw.response_id as string)
                    : undefined,
    };

    const narrativeBits: string[] = [];

    // Top-level "answer"-style fields some versions ship.
    for (const k of ['description', 'answer', 'text']) {
        const v = (raw as Record<string, unknown>)[k];
        if (typeof v === 'string' && v.trim()) {
            narrativeBits.push(v.trim());
        }
    }

    // Walk attachments. Each attachment is either a text block or a
    // query block (SQL + result). A single Genie response can have
    // multiple text attachments (e.g. a description plus a follow-up
    // suggestion); we concatenate them into one narrative.
    const attachments = Array.isArray(raw.attachments)
        ? (raw.attachments as Array<Record<string, unknown>>)
        : [];

    for (const att of attachments) {
        if (!att || typeof att !== 'object') continue;

        const text = att.text;
        if (text && typeof text === 'object') {
            const content = (text as Record<string, unknown>).content;
            if (typeof content === 'string' && content.trim()) {
                narrativeBits.push(content.trim());
            }
        } else if (typeof text === 'string' && text.trim()) {
            narrativeBits.push(text.trim());
        }

        const query = att.query;
        if (query && typeof query === 'object') {
            const q = query as Record<string, unknown>;
            // SQL string lives under ``query.query`` in the modern shape;
            // older payloads sometimes put the SQL directly in ``query``.
            if (typeof q.query === 'string' && q.query.trim() && !out.sql) {
                out.sql = q.query.trim();
            } else if (
                typeof q.statement === 'string' &&
                (q.statement as string).trim() &&
                !out.sql
            ) {
                out.sql = (q.statement as string).trim();
            }
            // A free-text description on the query itself often
            // restates the user's question — fold it into the narrative.
            if (typeof q.description === 'string' && q.description.trim()) {
                narrativeBits.push((q.description as string).trim());
            }
            // Tabular result. Multiple shapes in the wild:
            //   - q.statement_response.result.data_array + .schema.columns
            //   - q.result.data_array + q.result.columns
            //   - q.data_array + q.columns
            //   - q.result.data_typed_array (newer)
            const preview = _extractPreview(q);
            if (preview && !out.preview) out.preview = preview;
            // Chart spec, when Genie auto-visualized the answer.
            const chart = _extractChart(att, q);
            if (chart && !out.chartSpec) out.chartSpec = chart;
        }
    }

    // Some payloads carry a top-level chart spec instead of nesting it
    // under an attachment.
    if (!out.chartSpec) {
        const topChart = _extractChart(raw, undefined);
        if (topChart) out.chartSpec = topChart;
    }
    // Same for SQL.
    if (!out.sql && typeof raw.query === 'string' && raw.query.trim()) {
        out.sql = (raw.query as string).trim();
    }

    out.narrative = _dedupeNarrative(narrativeBits);
    return out;
}

function _extractPreview(q: Record<string, unknown>): GenieDataPreview | undefined {
    // Prefer the most-nested shape first since it's the canonical one.
    const stmt = q.statement_response;
    if (stmt && typeof stmt === 'object') {
        const result = (stmt as Record<string, unknown>).result;
        const manifest = (stmt as Record<string, unknown>).manifest;
        const cols = _columnsFromManifest(manifest) ?? _columnsFromResult(result);
        const rows = _rowsFromResult(result);
        if (cols && rows) {
            return _buildPreview(cols, rows, _totalRowsFromResult(result));
        }
    }
    const result = q.result;
    if (result && typeof result === 'object') {
        const cols = _columnsFromResult(result);
        const rows = _rowsFromResult(result);
        if (cols && rows) {
            return _buildPreview(cols, rows, _totalRowsFromResult(result));
        }
    }
    // Flat shape on the query itself.
    const flatRows = _rowsFromResult(q);
    const flatCols = _columnsFromResult(q);
    if (flatCols && flatRows) {
        return _buildPreview(flatCols, flatRows, _totalRowsFromResult(q));
    }
    return undefined;
}

function _columnsFromManifest(manifest: unknown): string[] | undefined {
    if (!manifest || typeof manifest !== 'object') return undefined;
    const schema = (manifest as Record<string, unknown>).schema;
    if (!schema || typeof schema !== 'object') return undefined;
    const cols = (schema as Record<string, unknown>).columns;
    if (!Array.isArray(cols)) return undefined;
    const out: string[] = [];
    for (const c of cols) {
        if (c && typeof c === 'object') {
            const name = (c as Record<string, unknown>).name;
            if (typeof name === 'string') out.push(name);
        }
    }
    return out.length > 0 ? out : undefined;
}

function _columnsFromResult(result: unknown): string[] | undefined {
    if (!result || typeof result !== 'object') return undefined;
    const cols = (result as Record<string, unknown>).columns;
    if (Array.isArray(cols)) {
        const named: string[] = [];
        for (const c of cols) {
            if (typeof c === 'string') named.push(c);
            else if (c && typeof c === 'object') {
                const name = (c as Record<string, unknown>).name;
                if (typeof name === 'string') named.push(name);
            }
        }
        if (named.length > 0) return named;
    }
    const schema = (result as Record<string, unknown>).schema;
    return _columnsFromManifest({ schema });
}

function _rowsFromResult(result: unknown): Array<Array<unknown>> | undefined {
    if (!result || typeof result !== 'object') return undefined;
    const r = result as Record<string, unknown>;
    // Newer Genie ships ``data_typed_array`` (array of {value} per cell);
    // we flatten it back to plain values for the preview table.
    if (Array.isArray(r.data_typed_array)) {
        return (r.data_typed_array as Array<Array<unknown>>).map((row) =>
            row.map((cell) => {
                if (cell && typeof cell === 'object' && 'str' in (cell as Record<string, unknown>)) {
                    return (cell as Record<string, unknown>).str;
                }
                if (cell && typeof cell === 'object' && 'value' in (cell as Record<string, unknown>)) {
                    return (cell as Record<string, unknown>).value;
                }
                return cell;
            }),
        );
    }
    if (Array.isArray(r.data_array)) {
        return r.data_array as Array<Array<unknown>>;
    }
    if (Array.isArray(r.rows)) {
        return r.rows as Array<Array<unknown>>;
    }
    return undefined;
}

function _totalRowsFromResult(result: unknown): number | undefined {
    if (!result || typeof result !== 'object') return undefined;
    const r = result as Record<string, unknown>;
    for (const k of ['row_count', 'total_row_count', 'total_rows']) {
        const v = r[k];
        if (typeof v === 'number' && Number.isFinite(v)) return v;
    }
    return undefined;
}

function _buildPreview(
    columns: string[],
    rows: Array<Array<unknown>>,
    total: number | undefined,
    cap: number = 10,
): GenieDataPreview {
    const truncated = rows.length > cap || (total !== undefined && total > rows.length);
    return {
        columns,
        rows: rows.slice(0, cap),
        totalRows: total,
        truncated,
    };
}

function _extractChart(
    parent: Record<string, unknown>,
    query: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
    // Look in a handful of known places. We accept anything that has
    // an ``encoding`` (the Vega-Lite signal) or a ``mark`` field.
    const candidates: unknown[] = [
        parent.chart,
        parent.chart_specification,
        parent.chartSpec,
        parent.vegalite_spec,
        query?.chart,
        query?.chart_specification,
    ];
    for (const c of candidates) {
        if (c && typeof c === 'object') {
            const obj = c as Record<string, unknown>;
            if (obj.encoding || obj.mark || obj.layer || obj.spec) return obj;
            // Some payloads wrap the spec under ``spec``; unwrap it.
            const inner = obj.spec;
            if (inner && typeof inner === 'object' && (inner as Record<string, unknown>).encoding) {
                return inner as Record<string, unknown>;
            }
        }
        if (typeof c === 'string') {
            try {
                const decoded = JSON.parse(c);
                if (decoded && typeof decoded === 'object') {
                    return decoded as Record<string, unknown>;
                }
            } catch {
                /* not JSON, ignore */
            }
        }
    }
    return undefined;
}

function _dedupeNarrative(bits: string[]): string {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const b of bits) {
        const norm = b.replace(/\s+/g, ' ').trim();
        if (!norm) continue;
        if (seen.has(norm)) continue;
        seen.add(norm);
        out.push(b);
    }
    return out.join('\n\n');
}
