/**
 * Client-side charting helpers shared by the in-chat chart surface.
 *
 * Databricks Genie (and the Genie MCP server) return *data*, never a
 * chart — it explicitly tells callers to "use your preferred
 * visualization tool". So we own chart construction here: infer column
 * types, pick a sensible default chart, and build a Vega-Lite spec that
 * the existing ``VegaLiteChart`` (vega-embed) renderer can draw.
 *
 * Everything is deterministic and dependency-free so it runs instantly
 * client-side — no LLM round-trip needed for the default chart or for
 * the interactive "re-graph" controls. The optional ``render_chart``
 * agent tool reuses the same encoding model so natural-language chart
 * requests and the UI controls converge on one representation.
 */

/** A tabular dataset: column names plus rows of cells parallel to them. */
export interface Dataset {
    columns: string[];
    rows: Array<Array<unknown>>;
}

/** Vega-Lite field type we map each column to. */
export type FieldType = 'quantitative' | 'temporal' | 'nominal';

/** The supported chart marks (kept intentionally small for the picker). */
export type ChartMark = 'bar' | 'line' | 'area' | 'point' | 'arc';

/** Aggregation applied to the y/theta measure when grouping by x/color. */
export type ChartAggregate = 'sum' | 'mean' | 'median' | 'min' | 'max' | 'count' | 'none';

/**
 * A compact, UI- and LLM-friendly chart description. This is the single
 * representation the auto-inference, the interactive controls, and the
 * ``render_chart`` tool all produce; ``buildVegaLiteSpec`` compiles it
 * into an actual Vega-Lite spec.
 */
export interface ChartEncoding {
    mark: ChartMark;
    /** Column for the x axis (or theta category for an arc/pie). */
    x?: string;
    /** Column for the y axis (or the measure for an arc/pie). */
    y?: string;
    /** Optional column to split series by color. */
    color?: string;
    /** Aggregation for the measure; 'none' plots raw values. */
    aggregate?: ChartAggregate;
    /** Optional chart title. */
    title?: string;
}

const _NUMERIC_RE = /^-?\d+(\.\d+)?$/;
// ISO-ish date / datetime, or YYYY-MM / YYYY shapes Genie commonly returns.
const _DATEISH_RE =
    /^\d{4}(-\d{2}(-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?)?)?$/;

/** Coerce a cell to a number when it cleanly represents one, else null. */
export function toNumber(v: unknown): number | null {
    if (typeof v === 'number') return Number.isFinite(v) ? v : null;
    if (typeof v === 'string') {
        const t = v.trim().replace(/,/g, '');
        if (t && _NUMERIC_RE.test(t)) {
            const n = Number(t);
            return Number.isFinite(n) ? n : null;
        }
    }
    return null;
}

function _looksTemporal(v: unknown): boolean {
    if (v instanceof Date) return true;
    if (typeof v === 'string') {
        const t = v.trim();
        return _DATEISH_RE.test(t) && !_NUMERIC_RE.test(t);
    }
    return false;
}

/**
 * Infer a Vega-Lite field type per column by sampling non-null cells.
 * A column is quantitative only when (nearly) every sampled value is a
 * clean number; temporal when values look like dates; otherwise nominal.
 */
export function inferFieldTypes(dataset: Dataset): Record<string, FieldType> {
    const out: Record<string, FieldType> = {};
    const sample = dataset.rows.slice(0, 50);
    dataset.columns.forEach((col, idx) => {
        let seen = 0;
        let numeric = 0;
        let temporal = 0;
        for (const row of sample) {
            const cell = row[idx];
            if (cell === null || cell === undefined || cell === '') continue;
            seen += 1;
            if (toNumber(cell) !== null) numeric += 1;
            else if (_looksTemporal(cell)) temporal += 1;
        }
        if (seen === 0) {
            out[col] = 'nominal';
        } else if (numeric / seen >= 0.8) {
            out[col] = 'quantitative';
        } else if (temporal / seen >= 0.6) {
            out[col] = 'temporal';
        } else {
            out[col] = 'nominal';
        }
    });
    return out;
}

/** Convert a column/rows dataset into an array of plain records for Vega. */
export function datasetToRecords(dataset: Dataset): Array<Record<string, unknown>> {
    return dataset.rows.map((row) => {
        const rec: Record<string, unknown> = {};
        dataset.columns.forEach((c, i) => {
            const cell = row[i];
            // Coerce clean numeric strings so Vega treats them as quantitative.
            const n = toNumber(cell);
            rec[c] = n !== null && typeof cell === 'string' ? n : cell;
        });
        return rec;
    });
}

/**
 * Pick a reasonable default chart for a dataset, mirroring the common
 * "TopGenie" heuristic: a temporal/categorical dimension on x, the first
 * measure on y, line for time series and bar otherwise. Falls back to a
 * single-value bar or a row-count bar when there's nothing obvious.
 */
export function inferChart(dataset: Dataset): ChartEncoding {
    const types = inferFieldTypes(dataset);
    const cols = dataset.columns;
    const quantitative = cols.filter((c) => types[c] === 'quantitative');
    const temporal = cols.filter((c) => types[c] === 'temporal');
    const nominal = cols.filter((c) => types[c] === 'nominal');

    // Time series: temporal dimension + a measure -> line.
    if (temporal.length >= 1 && quantitative.length >= 1) {
        return {
            mark: 'line',
            x: temporal[0],
            y: quantitative[0],
            color: nominal[0],
            aggregate: 'sum',
        };
    }
    // Category + measure -> bar (the most common case).
    if (nominal.length >= 1 && quantitative.length >= 1) {
        return {
            mark: 'bar',
            x: nominal[0],
            y: quantitative[0],
            color: nominal[1],
            aggregate: 'sum',
        };
    }
    // Two measures -> scatter.
    if (quantitative.length >= 2) {
        return { mark: 'point', x: quantitative[0], y: quantitative[1], aggregate: 'none' };
    }
    // One measure, one dimension already handled; one measure only -> bar of values.
    if (quantitative.length === 1 && cols.length >= 1) {
        const dim = cols.find((c) => c !== quantitative[0]) ?? quantitative[0];
        return { mark: 'bar', x: dim, y: quantitative[0], aggregate: 'sum' };
    }
    // Pure categorical -> count by the first category.
    if (nominal.length >= 1) {
        return { mark: 'bar', x: nominal[0], aggregate: 'count' };
    }
    return { mark: 'bar', x: cols[0], aggregate: 'count' };
}

/** True when a dataset has at least one row and one column — chartable. */
export function isChartable(dataset: Dataset | undefined | null): dataset is Dataset {
    return !!dataset && dataset.columns.length > 0 && dataset.rows.length > 0;
}

interface FieldDef {
    field?: string;
    type?: FieldType;
    aggregate?: string;
    title?: string;
}

/**
 * Compile a ``ChartEncoding`` into a Vega-Lite spec (without inline data —
 * the renderer is handed the records separately so the same spec can be
 * re-bound to a refreshed dataset). Encodings that don't apply to the
 * chosen mark are omitted so the spec stays valid.
 */
export function buildVegaLiteSpec(
    dataset: Dataset,
    encoding: ChartEncoding,
): Record<string, unknown> {
    const types = inferFieldTypes(dataset);
    const agg = encoding.aggregate && encoding.aggregate !== 'none' ? encoding.aggregate : undefined;

    const measure = (field: string | undefined): FieldDef | undefined => {
        if (encoding.aggregate === 'count') {
            return { aggregate: 'count', type: 'quantitative', title: 'Count' };
        }
        if (!field) return undefined;
        const def: FieldDef = { field, type: types[field] ?? 'quantitative' };
        if (agg && def.type === 'quantitative') def.aggregate = agg;
        return def;
    };

    const dimension = (field: string | undefined): FieldDef | undefined => {
        if (!field) return undefined;
        return { field, type: types[field] ?? 'nominal' };
    };

    const enc: Record<string, unknown> = {};

    if (encoding.mark === 'arc') {
        // Pie/donut: theta is the measure, color is the category.
        const theta = measure(encoding.y);
        if (theta) enc.theta = theta;
        const cat = dimension(encoding.color ?? encoding.x);
        if (cat) enc.color = cat;
    } else {
        const xDef = dimension(encoding.x);
        if (xDef) enc.x = xDef;
        const yDef =
            encoding.aggregate === 'count' ? measure(undefined) : measure(encoding.y);
        if (yDef) enc.y = yDef;
        const colorDef = dimension(encoding.color);
        if (colorDef) enc.color = colorDef;
    }

    const markObj: Record<string, unknown> = {
        type: encoding.mark === 'point' ? 'point' : encoding.mark,
        tooltip: true,
    };
    if (encoding.mark === 'line' || encoding.mark === 'area') markObj.point = true;
    if (encoding.mark === 'arc') markObj.innerRadius = 0;

    const spec: Record<string, unknown> = {
        mark: markObj,
        encoding: enc,
    };
    if (encoding.title) spec.title = encoding.title;
    return spec;
}

/**
 * Best-effort: read an existing Vega-Lite spec back into our compact
 * ``ChartEncoding`` so the interactive controls can seed from a spec that
 * Genie or the agent supplied. Unknown shapes degrade to a bar default.
 */
export function encodingFromSpec(
    spec: Record<string, unknown> | undefined,
    dataset: Dataset,
): ChartEncoding {
    if (!spec || typeof spec !== 'object') return inferChart(dataset);
    const markRaw = spec.mark;
    const markType =
        typeof markRaw === 'string'
            ? markRaw
            : markRaw && typeof markRaw === 'object'
                ? (markRaw as Record<string, unknown>).type
                : undefined;
    const mark = _coerceMark(markType);
    const enc = (spec.encoding as Record<string, unknown>) ?? {};
    const fieldOf = (channel: unknown): string | undefined => {
        if (channel && typeof channel === 'object') {
            const f = (channel as Record<string, unknown>).field;
            if (typeof f === 'string') return f;
        }
        return undefined;
    };
    const aggOf = (channel: unknown): ChartAggregate | undefined => {
        if (channel && typeof channel === 'object') {
            const a = (channel as Record<string, unknown>).aggregate;
            if (typeof a === 'string' && _AGGS.includes(a as ChartAggregate)) {
                return a as ChartAggregate;
            }
        }
        return undefined;
    };
    if (mark === 'arc') {
        return {
            mark,
            y: fieldOf(enc.theta),
            color: fieldOf(enc.color),
            aggregate: aggOf(enc.theta) ?? 'sum',
            title: _titleOf(spec),
        };
    }
    return {
        mark,
        x: fieldOf(enc.x),
        y: fieldOf(enc.y),
        color: fieldOf(enc.color),
        aggregate: aggOf(enc.y) ?? 'none',
        title: _titleOf(spec),
    };
}

const _AGGS: ChartAggregate[] = ['sum', 'mean', 'median', 'min', 'max', 'count', 'none'];

export const CHART_MARKS: Array<{ value: ChartMark; label: string }> = [
    { value: 'bar', label: 'Bar' },
    { value: 'line', label: 'Line' },
    { value: 'area', label: 'Area' },
    { value: 'point', label: 'Scatter' },
    { value: 'arc', label: 'Pie' },
];

export const CHART_AGGREGATES: Array<{ value: ChartAggregate; label: string }> = [
    { value: 'none', label: 'None (raw)' },
    { value: 'sum', label: 'Sum' },
    { value: 'mean', label: 'Average' },
    { value: 'median', label: 'Median' },
    { value: 'min', label: 'Min' },
    { value: 'max', label: 'Max' },
    { value: 'count', label: 'Count' },
];

function _coerceMark(v: unknown): ChartMark {
    if (v === 'bar' || v === 'line' || v === 'area' || v === 'arc') return v;
    if (v === 'point' || v === 'circle' || v === 'square') return 'point';
    return 'bar';
}

function _titleOf(spec: Record<string, unknown>): string | undefined {
    const t = spec.title;
    if (typeof t === 'string') return t;
    if (t && typeof t === 'object') {
        const text = (t as Record<string, unknown>).text;
        if (typeof text === 'string') return text;
    }
    return undefined;
}
