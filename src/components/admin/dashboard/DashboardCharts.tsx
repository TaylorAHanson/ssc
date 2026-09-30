import { useMemo, type ReactNode } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../../ui/card';
import { VegaLiteChart } from '../../chat/VegaLiteChart';
import { theme } from '../../../theme';
import type { Request } from '../../../types';
import {
    STATUS_GROUP_META, STATUS_GROUP_ORDER, isAutomated, parseTimestamp, statusGroup,
    type StatusCount, type StatusGroup, type TypeCompletion, type TypeCount,
} from './requestMetrics';

const MUTED_BAR = '#BFDBFE';
const AUTOMATED_COLOR = '#A78BFA';
const AXIS_CONFIG = { labelColor: '#6B7280', titleColor: '#6B7280', gridColor: '#F3F4F6', domainColor: '#E5E7EB', tickColor: '#E5E7EB' };
const BASE_CONFIG = { view: { stroke: null }, axis: AXIS_CONFIG, legend: { labelColor: '#374151' } };
const SCHEMA = 'https://vega.github.io/schema/vega-lite/v6.json';

export function ChartCard({ title, subtitle, action, empty, children }: {
    title: string;
    subtitle?: string;
    action?: ReactNode;
    empty?: boolean;
    children: ReactNode;
}) {
    return (
        <Card className="flex flex-col">
            <CardHeader className="p-4 pb-2 flex flex-row items-start justify-between space-y-0 gap-2">
                <div>
                    <CardTitle className="text-sm font-semibold text-gray-900">{title}</CardTitle>
                    {subtitle && <p className="text-xs text-gray-500 mt-0.5">{subtitle}</p>}
                </div>
                {action}
            </CardHeader>
            <CardContent className="p-4 pt-0 flex-1">
                {empty ? (
                    <div className="h-40 flex items-center justify-center text-sm text-gray-400">No data for this view</div>
                ) : children}
            </CardContent>
        </Card>
    );
}

export type OverTimeSeries = 'status' | 'source';

export function RequestsOverTimeChart({ rows, start, now, series, onStatusClick }: {
    rows: Request[];
    start: number | null;
    now: number;
    series: OverTimeSeries;
    onStatusClick: (group: StatusGroup) => void;
}) {
    const weekly = start === null || now - start > 45 * 24 * 3_600_000;
    const timeUnit = weekly ? 'yearweek' : 'yearmonthdate';

    const data = useMemo(() => rows.map((r) => ({
        created: parseTimestamp(r.createdAt),
        series: series === 'status' ? STATUS_GROUP_META[statusGroup(r)].label : isAutomated(r) ? 'Automated' : 'People',
    })), [rows, series]);

    const spec = useMemo(() => {
        const domain = series === 'status'
            ? STATUS_GROUP_ORDER.map((g) => STATUS_GROUP_META[g].label)
            : ['People', 'Automated'];
        const range = series === 'status'
            ? STATUS_GROUP_ORDER.map((g) => STATUS_GROUP_META[g].color)
            : [theme.colors.accent, AUTOMATED_COLOR];
        return {
            $schema: SCHEMA,
            config: BASE_CONFIG,
            mark: { type: 'bar', cursor: series === 'status' ? 'pointer' : 'default' },
            encoding: {
                x: {
                    field: 'created', type: 'temporal', timeUnit, title: null,
                    axis: { format: '%b %d', labelAngle: 0, labelOverlap: true },
                    ...(start !== null ? { scale: { domain: [start, now] } } : {}),
                },
                y: { aggregate: 'count', type: 'quantitative', title: 'Requests', axis: { tickMinStep: 1 } },
                color: { field: 'series', type: 'nominal', scale: { domain, range }, legend: { orient: 'bottom', title: null } },
                tooltip: [
                    { field: 'created', type: 'temporal', timeUnit, title: weekly ? 'Week of' : 'Date', format: '%b %d, %Y' },
                    { field: 'series', type: 'nominal', title: series === 'status' ? 'Status' : 'Source' },
                    { aggregate: 'count', type: 'quantitative', title: 'Requests' },
                ],
            },
        };
    }, [series, start, now, timeUnit, weekly]);

    const handleClick = (datum: Record<string, unknown>) => {
        if (series !== 'status') return;
        const group = STATUS_GROUP_ORDER.find((g) => STATUS_GROUP_META[g].label === datum.series);
        if (group) onStatusClick(group);
    };

    return <VegaLiteChart spec={spec} data={data} height={220} onDatumClick={handleClick} />;
}

export function TypeBarChart({ data, selected, onTypeClick }: {
    data: TypeCount[];
    selected: string | null;
    onTypeClick: (type: string) => void;
}) {
    const spec = useMemo(() => ({
        $schema: SCHEMA,
        config: BASE_CONFIG,
        height: Math.max(80, data.length * 26),
        mark: { type: 'bar', cursor: 'pointer', cornerRadiusEnd: 3 },
        encoding: {
            y: { field: 'label', type: 'nominal', sort: '-x', title: null, axis: { labelLimit: 180 } },
            x: { field: 'count', type: 'quantitative', title: 'Requests', axis: { tickMinStep: 1 } },
            color: selected
                ? { condition: { test: `datum.type === ${JSON.stringify(selected)}`, value: theme.colors.accent }, value: MUTED_BAR }
                : { value: theme.colors.accent },
            tooltip: [{ field: 'label', title: 'Type' }, { field: 'count', title: 'Requests' }],
        },
    }), [data.length, selected]);

    return (
        <VegaLiteChart
            spec={spec}
            data={data}
            height={Math.max(80, data.length * 26) + 40}
            onDatumClick={(d) => typeof d.type === 'string' && onTypeClick(d.type)}
        />
    );
}

export function StatusDonut({ data, selected, onStatusClick }: {
    data: StatusCount[];
    selected: string | null;
    onStatusClick: (group: StatusGroup) => void;
}) {
    const spec = useMemo(() => ({
        $schema: SCHEMA,
        config: BASE_CONFIG,
        mark: { type: 'arc', innerRadius: 55, cursor: 'pointer', stroke: '#fff', strokeWidth: 2 },
        encoding: {
            theta: { field: 'count', type: 'quantitative', stack: true },
            color: {
                field: 'label', type: 'nominal', title: null,
                scale: { domain: STATUS_GROUP_ORDER.map((g) => STATUS_GROUP_META[g].label), range: STATUS_GROUP_ORDER.map((g) => STATUS_GROUP_META[g].color) },
                legend: { orient: 'right' },
            },
            ...(selected ? { opacity: { condition: { test: `datum.group === ${JSON.stringify(selected)}`, value: 1 }, value: 0.35 } } : {}),
            tooltip: [{ field: 'label', title: 'Status' }, { field: 'count', title: 'Requests' }],
        },
    }), [selected]);

    return (
        <VegaLiteChart
            spec={spec}
            data={data}
            height={220}
            onDatumClick={(d) => {
                const group = STATUS_GROUP_ORDER.find((g) => g === d.group);
                if (group) onStatusClick(group);
            }}
        />
    );
}

export function CompletionByTypeChart({ data, selected, onTypeClick }: {
    data: TypeCompletion[];
    selected: string | null;
    onTypeClick: (type: string) => void;
}) {
    const maxHours = Math.max(0, ...data.map((d) => d.medianHours));
    const unit = maxHours < 2 ? { name: 'minutes', perHour: 60 } : maxHours < 72 ? { name: 'hours', perHour: 1 } : { name: 'days', perHour: 1 / 24 };
    const values = useMemo(
        () => data.map((d) => ({ ...d, value: d.medianHours * unit.perHour })),
        [data, unit.perHour],
    );

    const spec = useMemo(() => ({
        $schema: SCHEMA,
        config: BASE_CONFIG,
        height: Math.max(80, data.length * 26),
        mark: { type: 'bar', cursor: 'pointer', cornerRadiusEnd: 3 },
        encoding: {
            y: { field: 'label', type: 'nominal', sort: '-x', title: null, axis: { labelLimit: 180 } },
            x: { field: 'value', type: 'quantitative', title: `Median ${unit.name} to complete`, axis: { format: '~r', labelOverlap: true } },
            color: selected
                ? { condition: { test: `datum.type === ${JSON.stringify(selected)}`, value: '#10B981' }, value: '#A7F3D0' }
                : { value: '#10B981' },
            tooltip: [
                { field: 'label', title: 'Type' },
                { field: 'display', title: 'Median' },
                { field: 'count', title: 'Completed' },
            ],
        },
    }), [data.length, selected, unit.name]);

    return (
        <VegaLiteChart
            spec={spec}
            data={values}
            height={Math.max(80, data.length * 26) + 40}
            onDatumClick={(d) => typeof d.type === 'string' && onTypeClick(d.type)}
        />
    );
}
