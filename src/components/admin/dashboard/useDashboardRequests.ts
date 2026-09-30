import { useCallback, useEffect, useRef, useState } from 'react';
import { getRequestSummaries } from '../../../services/api';
import { useVisibleInterval } from '../../../hooks/useVisibleInterval';
import type { Request } from '../../../types';
import { AUTOMATED_REQUEST_TYPES, DAY_MS } from './requestMetrics';

const PAGE_LIMIT = 1000;
const REFRESH_MS = 60_000;

interface LoadedState {
    days: number | null;
    rows: Request[];
    fetchedAt: number;
    truncated: boolean;
    error: string | null;
}

/**
 * Loads people's requests (all time) separately from everything created in the
 * range, so frequent automated runs can't push people's requests out of the page.
 */
export function useDashboardRequests(days: number | null) {
    const [state, setState] = useState<LoadedState | null>(null);
    const latestCall = useRef(0);

    const load = useCallback(async () => {
        const call = ++latestCall.current;
        const fetchedAt = Date.now();
        try {
            const [people, recent] = await Promise.all([
                getRequestSummaries({ limit: PAGE_LIMIT, excludeTypes: [...AUTOMATED_REQUEST_TYPES] }),
                getRequestSummaries({
                    limit: PAGE_LIMIT,
                    createdAfter: days === null ? undefined : new Date(fetchedAt - days * DAY_MS),
                }),
            ]);
            if (call !== latestCall.current) return;
            const byId = new Map<string, Request>();
            [...people.items, ...recent.items].forEach((r) => byId.set(r.id, r));
            setState({
                days,
                rows: [...byId.values()],
                fetchedAt,
                truncated: people.total > people.items.length || recent.total > recent.items.length,
                error: null,
            });
        } catch (err) {
            if (call !== latestCall.current) return;
            setState((prev) => ({
                days,
                rows: prev?.rows ?? [],
                fetchedAt: prev?.fetchedAt ?? fetchedAt,
                truncated: prev?.truncated ?? false,
                error: err instanceof Error ? err.message : String(err),
            }));
        }
    }, [days]);

    useEffect(() => {
        void load();
    }, [load]);
    useVisibleInterval(load, REFRESH_MS);

    return {
        rows: state?.rows ?? [],
        fetchedAt: state?.fetchedAt ?? null,
        truncated: state?.truncated ?? false,
        error: state?.error ?? null,
        loading: state === null || state.days !== days,
        reload: load,
    };
}
