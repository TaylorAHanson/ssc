/**
 * Renders a Vega-Lite chart spec produced by Databricks Genie.
 *
 * The Vega-Lite library is heavy (~500 KB minified) and most chats
 * never need it, so we lazy-load `vega-embed` on mount. Charts render
 * once the dynamic import resolves; users see a tiny skeleton while
 * we wait, then the chart fades in.
 *
 * Genie's chart specifications follow the standard Vega-Lite schema —
 * we don't transform them, just hand them to vega-embed and let it do
 * its thing. If the spec is malformed or the inline data is missing,
 * we surface a small error instead of crashing the chat.
 */
import { useEffect, useRef, useState } from 'react';

export interface VegaLiteChartProps {
    /** The Vega-Lite spec object as Genie returned it. */
    spec: Record<string, unknown>;
    /** Optional explicit data table (rows + columns) merged into the spec. */
    data?: Array<Record<string, unknown>>;
    /** Pixel height the chart should target. Defaults to 280px. */
    height?: number;
}

export function VegaLiteChart({ spec, data, height = 280 }: VegaLiteChartProps) {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [ready, setReady] = useState(false);

    useEffect(() => {
        let cancelled = false;
        // Track the cleanup handle returned by vega-embed so we can
        // tear the rendered view down on unmount or spec change.
        let viewFinalize: (() => void) | undefined;

        // Compose the final spec: if Genie returned the chart and the
        // data separately we splice them together so vega-embed has a
        // self-contained spec to render.
        const finalSpec: Record<string, unknown> =
            data && data.length > 0
                ? { ...spec, data: { values: data } }
                : { ...spec };

        // Defensive defaults so an under-specified spec still renders.
        if (!finalSpec.width) finalSpec.width = 'container';
        if (!finalSpec.height) finalSpec.height = height;
        // Vega-Lite respects ``$schema`` for autocompletion in editors;
        // adding it doesn't change runtime behavior.
        if (!finalSpec.$schema) {
            finalSpec.$schema = 'https://vega.github.io/schema/vega-lite/v5.json';
        }

        (async () => {
            try {
                // Dynamic import keeps Vega out of the main bundle. We
                // only pay the load cost when a chart actually renders.
                const { default: embed } = await import('vega-embed');
                if (cancelled || !containerRef.current) return;
                const result = await embed(containerRef.current, finalSpec, {
                    actions: false,
                    renderer: 'canvas',
                    // Auto-fit the container width so the chart
                    // re-flows when the chat column resizes.
                    config: { autosize: { type: 'fit', contains: 'padding' } },
                });
                if (cancelled) {
                    result.finalize?.();
                    return;
                }
                viewFinalize = result.finalize;
                setReady(true);
            } catch (err) {
                if (cancelled) return;
                setError(err instanceof Error ? err.message : String(err));
            }
        })();

        return () => {
            cancelled = true;
            viewFinalize?.();
        };
    }, [spec, data, height]);

    if (error) {
        return (
            <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
                Chart preview unavailable: <span className="font-mono">{error}</span>
            </div>
        );
    }

    return (
        <div className="relative w-full" style={{ minHeight: height }}>
            {!ready && (
                <div
                    className="absolute inset-0 flex items-center justify-center text-xs text-gray-400 animate-pulse"
                    aria-hidden
                >
                    Rendering chart…
                </div>
            )}
            <div ref={containerRef} className="w-full" />
        </div>
    );
}
