/**
 * Detects chart-renderable content in an arbitrary agent tool result so
 * the chat can render a chart below *any* tool pill — not just Genie.
 *
 * Two producers feed this:
 *   1. The ``render_chart`` agent tool, which emits a compact
 *      ``chart_directive`` (mark + field encodings) meant to be bound to
 *      the conversation's most recent dataset (so "graph that as a line"
 *      reuses the rows Genie already returned).
 *   2. Any tool that returns tabular data (``columns``/``rows`` or an
 *      array of record objects) or an inline Vega-Lite spec.
 */
import {
    type ChartEncoding,
    type ChartMark,
    type Dataset,
} from '../../lib/charting';
import { parseGenieResult } from './parseGenieResult';

export interface ToolChart {
    /** Explicit dataset carried by the tool result (if any). */
    dataset?: Dataset;
    /** Inline Vega-Lite spec carried by the tool result (if any). */
    spec?: Record<string, unknown>;
    /** Compact encoding to seed the interactive controls from. */
    encoding?: Partial<ChartEncoding>;
    /**
     * When true the chart should bind to the conversation's most recent
     * dataset rather than any data on the result itself (the render_chart
     * "re-graph the last answer" case).
     */
    bindLast?: boolean;
}

const _SPEC_KEYS = ['chart_specification', 'chart', 'vegalite_spec', 'chartSpec', 'spec'];
const _VALID_MARKS: ChartMark[] = ['bar', 'line', 'area', 'point', 'arc'];

/**
 * Inspect a tool result and return a ``ToolChart`` when it carries
 * something renderable, else ``undefined``. Pure + defensive — never
 * throws on odd shapes.
 */
export function parseToolChart(result: unknown, toolName?: string): ToolChart | undefined {
    if (!result || typeof result !== 'object') return undefined;
    const obj = result as Record<string, unknown>;

    // 1. render_chart directive — the canonical natural-language path.
    const directive = obj.chart_directive;
    if (directive && typeof directive === 'object') {
        const enc = _encodingFromDirective(directive as Record<string, unknown>);
        const inline = _datasetFromAny(obj.data) ?? _datasetFromColumnsRows(obj);
        return { encoding: enc, dataset: inline, bindLast: !inline };
    }

    // 2. Inline Vega-Lite spec.
    for (const k of _SPEC_KEYS) {
        const candidate = obj[k];
        if (candidate && typeof candidate === 'object') {
            const c = candidate as Record<string, unknown>;
            if (c.mark || c.encoding || c.layer) {
                return { spec: c, dataset: _datasetFromAny(obj.data) ?? _datasetFromColumnsRows(obj) };
            }
        }
    }

    // 3. Bare tabular data on the result.
    const dataset = _datasetFromColumnsRows(obj) ?? _datasetFromAny(obj.data) ?? _datasetFromAny(obj.rows);
    if (dataset) return { dataset };

    // Tools explicitly named for charting always get a chance, even if the
    // shape was unusual — fall back to binding the last dataset.
    if (toolName === 'render_chart') return { bindLast: true };

    return undefined;
}

/**
 * Minimal view of a tool chat message needed to recover its dataset. Kept
 * structural so ChatView's richer ``DisplayMessage`` satisfies it directly.
 */
export interface ToolMessageLike {
    /** Resolved Genie poll payload (carries Genie's rows). */
    genieResult?: Record<string, unknown>;
    /** Raw result of a synchronous tool (e.g. run_sql's columns/rows). */
    toolResult?: unknown;
    /** A chart this message already resolved (cheapest source of its dataset). */
    chart?: { dataset?: Dataset };
}

/**
 * Pull the most recent chartable dataset out of earlier chat messages.
 *
 * Walks newest-first and accepts a dataset from ANY data-producing tool —
 * Genie answers *and* synchronous tools like ``run_sql`` — so "graph that as a
 * line" re-graphs whatever the latest data answer was, regardless of source.
 */
export function findLastDataset(
    tools: Array<ToolMessageLike | undefined>,
): Dataset | undefined {
    for (let i = tools.length - 1; i >= 0; i -= 1) {
        const ds = datasetFromToolMessage(tools[i]);
        if (ds) return ds;
    }
    return undefined;
}

/** Recover a dataset from a single tool message, whatever its source. */
export function datasetFromToolMessage(
    m: ToolMessageLike | undefined,
): Dataset | undefined {
    if (!m) return undefined;
    // Prefer an already-resolved chart dataset — it's exactly what rendered.
    const resolved = m.chart?.dataset;
    if (resolved && resolved.columns.length && resolved.rows.length) return resolved;
    // Genie answers carry their data in genieResult.
    const fromGenie = datasetFromGenieResult(m.genieResult);
    if (fromGenie) return fromGenie;
    // Any other tool (run_sql, etc.) carries tabular data on its raw result.
    const parsed = parseToolChart(m.toolResult);
    if (parsed?.dataset) return parsed.dataset;
    return undefined;
}

/** Build a ``Dataset`` from a Genie poll result's tabular preview. */
export function datasetFromGenieResult(
    result: Record<string, unknown> | undefined,
): Dataset | undefined {
    if (!result) return undefined;
    const parsed = parseGenieResult(result);
    if (!parsed.preview) return undefined;
    const rows = parsed.preview.allRows ?? parsed.preview.rows;
    if (!parsed.preview.columns.length || !rows.length) return undefined;
    return { columns: parsed.preview.columns, rows };
}

function _encodingFromDirective(d: Record<string, unknown>): Partial<ChartEncoding> {
    const out: Partial<ChartEncoding> = {};
    const mark = d.mark;
    if (typeof mark === 'string' && _VALID_MARKS.includes(mark as ChartMark)) {
        out.mark = mark as ChartMark;
    }
    for (const k of ['x', 'y', 'color', 'title'] as const) {
        const v = d[k];
        if (typeof v === 'string' && v.trim()) out[k] = v.trim();
    }
    const agg = d.aggregate;
    if (typeof agg === 'string') out.aggregate = agg as ChartEncoding['aggregate'];
    return out;
}

function _datasetFromColumnsRows(obj: Record<string, unknown>): Dataset | undefined {
    const cols = obj.columns;
    const rows = obj.rows;
    if (Array.isArray(cols) && Array.isArray(rows) && cols.every((c) => typeof c === 'string')) {
        if (rows.length && Array.isArray(rows[0])) {
            return { columns: cols as string[], rows: rows as Array<Array<unknown>> };
        }
        // rows as record objects keyed by column.
        if (rows.length && rows[0] && typeof rows[0] === 'object') {
            return _datasetFromAny(rows);
        }
    }
    return undefined;
}

/** Accept an array of record objects and reshape to a columns/rows dataset. */
function _datasetFromAny(data: unknown): Dataset | undefined {
    if (!Array.isArray(data) || data.length === 0) return undefined;
    const first = data[0];
    if (!first || typeof first !== 'object' || Array.isArray(first)) return undefined;
    const columns = Object.keys(first as Record<string, unknown>);
    if (columns.length === 0) return undefined;
    const rows = data.map((rec) =>
        columns.map((c) => (rec as Record<string, unknown>)[c]),
    );
    return { columns, rows };
}
