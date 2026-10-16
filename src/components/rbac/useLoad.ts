import { useCallback, useEffect, useState } from 'react';

interface Loaded<T> {
  load: unknown;
  tick: number;
  data?: T;
  error?: unknown;
}

/**
 * Runs `load` (keep it stable with useCallback) and tracks its result. `loading` is
 * derived from whether the stored result belongs to the current `load` and reload
 * tick, so nothing is set synchronously inside the effect. A reload keeps showing
 * the previous data; a different `load` (e.g. another sheet) starts empty.
 */
export function useLoad<T>(load: (() => Promise<T>) | null) {
  const [tick, setTick] = useState(0);
  const [result, setResult] = useState<Loaded<T>>({ load: null, tick: -1 });

  useEffect(() => {
    if (!load) return;
    let active = true;
    load().then(
      (data) => {
        if (active) setResult({ load, tick, data });
      },
      (error: unknown) => {
        if (active) setResult((prev) => ({ load, tick, data: prev.load === load ? prev.data : undefined, error }));
      },
    );
    return () => {
      active = false;
    };
  }, [load, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const sameLoad = result.load === load;
  const loading = !!load && (!sameLoad || result.tick !== tick);
  return {
    data: sameLoad ? result.data : undefined,
    error: loading ? undefined : result.error,
    loading,
    reload,
  };
}
