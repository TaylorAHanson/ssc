/**
 * Interactive, in-chat chart surface — the "ephemeral dashboard".
 *
 * Given a tabular dataset (columns + rows), this renders a Vega-Lite
 * chart the user can re-graph on the fly: change the mark (bar / line /
 * area / scatter / pie), swap the x / y / color fields, and pick an
 * aggregation — all recomputed client-side with no re-query. It seeds
 * from an optional initial spec (e.g. one Genie or the agent supplied)
 * and otherwise auto-infers a sensible default from the column types.
 *
 * The chart state is intentionally ephemeral: it lives on the rendered
 * message and isn't persisted. Re-graphing is instant because we only
 * rebuild the Vega-Lite spec and hand the same in-memory rows to
 * ``VegaLiteChart``.
 */
import { useMemo, useState } from 'react';
import { BarChart3, Settings2 } from 'lucide-react';

import { VegaLiteChart } from './VegaLiteChart';
import {
    CHART_AGGREGATES,
    CHART_MARKS,
    buildVegaLiteSpec,
    datasetToRecords,
    encodingFromSpec,
    inferChart,
    inferFieldTypes,
    type ChartAggregate,
    type ChartEncoding,
    type ChartMark,
    type Dataset,
} from '../../lib/charting';

export interface ChartPanelProps {
    dataset: Dataset;
    /** Optional Vega-Lite spec to seed the controls from (Genie/agent supplied). */
    initialSpec?: Record<string, unknown>;
    /** Optional compact encoding to seed from (e.g. the render_chart tool). */
    initialEncoding?: Partial<ChartEncoding>;
    height?: number;
    /** Start with the field controls expanded. */
    defaultControlsOpen?: boolean;
}

const selectClass =
    'text-xs border border-gray-300 rounded-md px-2 py-1 bg-white focus:outline-none focus:ring-2 focus:ring-accent';

export function ChartPanel({
    dataset,
    initialSpec,
    initialEncoding,
    height = 300,
    defaultControlsOpen = false,
}: ChartPanelProps) {
    const types = useMemo(() => inferFieldTypes(dataset), [dataset]);
    const records = useMemo(() => datasetToRecords(dataset), [dataset]);

    // Seed the encoding: explicit encoding wins, then a supplied spec,
    // then auto-inference. Recomputed only when the inputs change.
    const seed = useMemo<ChartEncoding>(() => {
        const base = initialSpec
            ? encodingFromSpec(initialSpec, dataset)
            : inferChart(dataset);
        return { ...base, ...stripUndefined(initialEncoding) };
    }, [initialSpec, initialEncoding, dataset]);

    const [encoding, setEncoding] = useState<ChartEncoding>(seed);
    const [controlsOpen, setControlsOpen] = useState(defaultControlsOpen);

    const spec = useMemo(() => buildVegaLiteSpec(dataset, encoding), [dataset, encoding]);

    const set = (patch: Partial<ChartEncoding>) => setEncoding((e) => ({ ...e, ...patch }));

    const isArc = encoding.mark === 'arc';
    const showAggregate = encoding.mark !== 'point';

    return (
        <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                    <BarChart3 className="w-3.5 h-3.5" />
                    Chart
                </div>
                <button
                    type="button"
                    onClick={() => setControlsOpen((v) => !v)}
                    className="inline-flex items-center gap-1 text-[11px] font-medium text-gray-500 hover:text-gray-700"
                    aria-expanded={controlsOpen}
                    title="Customize this chart"
                >
                    <Settings2 className="w-3.5 h-3.5" />
                    {controlsOpen ? 'Hide controls' : 'Customize'}
                </button>
            </div>

            {controlsOpen && (
                <div className="flex flex-wrap items-end gap-2 bg-gray-50 border border-gray-200 rounded-md p-2">
                    <Field label="Type">
                        <select
                            className={selectClass}
                            value={encoding.mark}
                            onChange={(e) => set({ mark: e.target.value as ChartMark })}
                        >
                            {CHART_MARKS.map((m) => (
                                <option key={m.value} value={m.value}>
                                    {m.label}
                                </option>
                            ))}
                        </select>
                    </Field>

                    <Field label={isArc ? 'Category' : 'X axis'}>
                        <select
                            className={selectClass}
                            value={(isArc ? encoding.color : encoding.x) ?? ''}
                            onChange={(e) =>
                                set(isArc ? { color: e.target.value || undefined } : { x: e.target.value || undefined })
                            }
                        >
                            <option value="">—</option>
                            {dataset.columns.map((c) => (
                                <option key={c} value={c}>
                                    {c} ({types[c][0]})
                                </option>
                            ))}
                        </select>
                    </Field>

                    <Field label={isArc ? 'Value' : 'Y axis'}>
                        <select
                            className={selectClass}
                            value={encoding.y ?? ''}
                            onChange={(e) => set({ y: e.target.value || undefined })}
                            disabled={encoding.aggregate === 'count'}
                        >
                            <option value="">—</option>
                            {dataset.columns.map((c) => (
                                <option key={c} value={c}>
                                    {c} ({types[c][0]})
                                </option>
                            ))}
                        </select>
                    </Field>

                    {!isArc && (
                        <Field label="Color">
                            <select
                                className={selectClass}
                                value={encoding.color ?? ''}
                                onChange={(e) => set({ color: e.target.value || undefined })}
                            >
                                <option value="">—</option>
                                {dataset.columns.map((c) => (
                                    <option key={c} value={c}>
                                        {c}
                                    </option>
                                ))}
                            </select>
                        </Field>
                    )}

                    {showAggregate && (
                        <Field label="Aggregate">
                            <select
                                className={selectClass}
                                value={encoding.aggregate ?? 'none'}
                                onChange={(e) => set({ aggregate: e.target.value as ChartAggregate })}
                            >
                                {CHART_AGGREGATES.map((a) => (
                                    <option key={a.value} value={a.value}>
                                        {a.label}
                                    </option>
                                ))}
                            </select>
                        </Field>
                    )}
                </div>
            )}

            <div className="bg-white border border-gray-200 rounded-md p-2">
                <VegaLiteChart spec={spec} data={records} height={height} />
            </div>
        </div>
    );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <label className="flex flex-col gap-0.5">
            <span className="text-[10px] font-medium uppercase tracking-wide text-gray-400">
                {label}
            </span>
            {children}
        </label>
    );
}

function stripUndefined<T extends object>(obj: T | undefined): Partial<T> {
    if (!obj) return {};
    const out: Partial<T> = {};
    (Object.keys(obj) as Array<keyof T>).forEach((k) => {
        if (obj[k] !== undefined) out[k] = obj[k];
    });
    return out;
}
