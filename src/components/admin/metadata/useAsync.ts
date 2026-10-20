import { useCallback, useEffect, useState } from 'react';

interface Loaded<T> {
  load: unknown;
  token: unknown;
  tick: number;
  data?: T;
  error?: unknown;
}

/**
 * Runs `load` (keep it stable with useCallback/useMemo) and tracks its result without
 * setting state synchronously in the effect. A change of `token` reloads (e.g. after a
 * save). With `keepPrevious`, a new `load` (another page or filter) keeps showing the
 * last data while it runs, so tables don't flash.
 */
export function useAsync<T>(load: (() => Promise<T>) | null, opts: { keepPrevious?: boolean; token?: unknown } = {}) {
  const { keepPrevious, token } = opts;
  const [tick, setTick] = useState(0);
  const [result, setResult] = useState<Loaded<T>>({ load: null, token: undefined, tick: -1 });

  useEffect(() => {
    if (!load) return;
    let active = true;
    load().then(
      (data) => {
        if (active) setResult({ load, token, tick, data });
      },
      (error: unknown) => {
        if (active) setResult((prev) => ({ load, token, tick, data: prev.load === load ? prev.data : undefined, error }));
      },
    );
    return () => {
      active = false;
    };
  }, [load, token, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const sameLoad = result.load === load;
  const loading = !!load && (!sameLoad || result.token !== token || result.tick !== tick);
  return {
    data: sameLoad || keepPrevious ? result.data : undefined,
    error: loading ? undefined : result.error,
    loading,
    reload,
  };
}

/** Debounces a value; the returned value lags `value` by `ms`. */
export function useDebounced<T>(value: T, ms: number): T {
  const [state, setState] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setState(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return state;
}
