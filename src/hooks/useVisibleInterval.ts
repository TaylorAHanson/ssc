/**
 * `useVisibleInterval` — a `setInterval` that pauses while the tab is hidden.
 *
 * Polling a background tab spends network, battery and backend load on data
 * nobody is looking at. When the tab becomes visible again the callback runs
 * once immediately (so the view catches up) and the interval resumes.
 *
 * The latest `callback` is always used, so callers don't need to memoize it;
 * only `delayMs` / `enabled` changes restart the timer.
 */
import { useEffect, useRef } from 'react';

export function useVisibleInterval(
    callback: () => void,
    delayMs: number,
    enabled: boolean = true,
): void {
    const callbackRef = useRef(callback);
    useEffect(() => {
        callbackRef.current = callback;
    }, [callback]);

    useEffect(() => {
        if (!enabled) return undefined;

        let timer: ReturnType<typeof setInterval> | null = null;
        const start = () => {
            if (timer === null) timer = setInterval(() => callbackRef.current(), delayMs);
        };
        const stop = () => {
            if (timer !== null) {
                clearInterval(timer);
                timer = null;
            }
        };
        const onVisibilityChange = () => {
            if (document.hidden) {
                stop();
            } else {
                callbackRef.current();
                start();
            }
        };

        if (!document.hidden) start();
        document.addEventListener('visibilitychange', onVisibilityChange);
        return () => {
            stop();
            document.removeEventListener('visibilitychange', onVisibilityChange);
        };
    }, [delayMs, enabled]);
}
