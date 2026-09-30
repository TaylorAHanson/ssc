import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { RANGE_OPTIONS, isStatusFilter, type RangeKey, type StatusFilter } from './requestMetrics';

export interface DashboardFilters {
    range: RangeKey;
    showAutomated: boolean;
    status: StatusFilter | null;
    type: string | null;
    requester: string | null;
}

const DEFAULT_RANGE: RangeKey = '30d';

/** Dashboard filters, persisted in the URL query so a filtered view can be shared. */
export function useDashboardFilters(): [DashboardFilters, (patch: Partial<DashboardFilters>) => void] {
    const [params, setParams] = useSearchParams();

    const filters = useMemo<DashboardFilters>(() => {
        const range = params.get('range');
        const status = params.get('status');
        return {
            range: RANGE_OPTIONS.some((o) => o.key === range) ? (range as RangeKey) : DEFAULT_RANGE,
            showAutomated: params.get('automated') === 'show',
            status: isStatusFilter(status) ? status : null,
            type: params.get('type'),
            requester: params.get('requester'),
        };
    }, [params]);

    const update = useCallback((patch: Partial<DashboardFilters>) => {
        setParams((prev) => {
            const next = new URLSearchParams(prev);
            const put = (key: string, value: string | null) => {
                if (value) next.set(key, value);
                else next.delete(key);
            };
            if ('range' in patch) put('range', patch.range === DEFAULT_RANGE ? null : patch.range ?? null);
            if ('showAutomated' in patch) put('automated', patch.showAutomated ? 'show' : null);
            if ('status' in patch) put('status', patch.status ?? null);
            if ('type' in patch) put('type', patch.type ?? null);
            if ('requester' in patch) put('requester', patch.requester ?? null);
            return next;
        }, { replace: true });
    }, [setParams]);

    return [filters, update];
}
