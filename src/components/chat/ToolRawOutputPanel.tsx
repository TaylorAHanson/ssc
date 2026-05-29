/**
 * Collapsible "Raw output" panel rendered below every synchronous
 * tool pill that returned a body.
 *
 * The agent's natural-language response paraphrases the data the tool
 * returned, which is great for chat flow but hides what actually came
 * back. SAs / advanced users frequently want to verify "did the tool
 * really return this row?" — surfacing the raw JSON inline saves them
 * from rereading server logs. The panel is collapsed by default so it
 * doesn't crowd the conversation; one click expands a syntax-light
 * monospace dump of the payload (and the arguments that produced it).
 *
 * Genie tool calls have their own dedicated ``GenieDetailsPanel``
 * which renders SQL, a result table, and any chart spec — those tools
 * don't render this panel.
 */
import { useMemo, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Copy } from 'lucide-react';

export interface ToolRawOutputPanelProps {
    toolName?: string;
    toolArguments?: Record<string, unknown>;
    result: unknown;
}

const MAX_DISPLAY_CHARS = 40_000;

function safeStringify(value: unknown): string {
    if (typeof value === 'string') return value;
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}

function summarize(result: unknown): string {
    if (Array.isArray(result)) return `${result.length}-item array`;
    if (result && typeof result === 'object') {
        const keys = Object.keys(result as Record<string, unknown>);
        if (keys.length === 0) return 'empty object';
        return `${keys.length} field${keys.length === 1 ? '' : 's'}`;
    }
    if (typeof result === 'string') return `${result.length} chars`;
    return typeof result;
}

export function ToolRawOutputPanel({
    toolName,
    toolArguments,
    result,
}: ToolRawOutputPanelProps) {
    const [open, setOpen] = useState(false);
    const [copied, setCopied] = useState(false);

    const { rendered, truncated, fullSize } = useMemo(() => {
        const full = safeStringify(result);
        if (full.length <= MAX_DISPLAY_CHARS) {
            return { rendered: full, truncated: false, fullSize: full.length };
        }
        return {
            rendered:
                full.slice(0, MAX_DISPLAY_CHARS) +
                `\n\n…[truncated for display: showing first ${MAX_DISPLAY_CHARS.toLocaleString()} of ${full.length.toLocaleString()} characters]`,
            truncated: true,
            fullSize: full.length,
        };
    }, [result]);

    const argsRendered = useMemo(() => {
        if (!toolArguments || Object.keys(toolArguments).length === 0) return null;
        return safeStringify(toolArguments);
    }, [toolArguments]);

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(
                typeof result === 'string' ? result : safeStringify(result),
            );
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked — no fallback worth the complexity here */
        }
    };

    return (
        <div className="mt-1 w-full max-w-[80%]">
            <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                className="inline-flex items-center gap-1 text-[11px] font-medium text-gray-500 hover:text-gray-700 transition-colors"
            >
                {open ? (
                    <ChevronDown className="h-3 w-3" />
                ) : (
                    <ChevronRight className="h-3 w-3" />
                )}
                Raw output
                <span className="text-gray-400 font-normal">
                    ({summarize(result)}
                    {truncated ? ` · ${fullSize.toLocaleString()} chars` : ''})
                </span>
            </button>
            {open && (
                <div className="mt-1 rounded-md border border-gray-200 bg-gray-50/80 overflow-hidden">
                    <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-200 bg-white/60">
                        <span className="text-[11px] font-mono text-gray-500">
                            {toolName ?? 'tool'}
                        </span>
                        <button
                            type="button"
                            onClick={handleCopy}
                            className="inline-flex items-center gap-1 text-[11px] text-gray-500 hover:text-gray-700 transition-colors"
                        >
                            {copied ? (
                                <>
                                    <Check className="h-3 w-3" /> Copied
                                </>
                            ) : (
                                <>
                                    <Copy className="h-3 w-3" /> Copy
                                </>
                            )}
                        </button>
                    </div>
                    {argsRendered && (
                        <div className="px-3 py-2 border-b border-gray-200">
                            <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">
                                Arguments
                            </div>
                            <pre className="text-[11px] font-mono text-gray-700 whitespace-pre-wrap break-all max-h-40 overflow-auto">
                                {argsRendered}
                            </pre>
                        </div>
                    )}
                    <div className="px-3 py-2">
                        <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">
                            Result
                        </div>
                        <pre className="text-[11px] font-mono text-gray-800 whitespace-pre-wrap break-all max-h-96 overflow-auto">
                            {rendered}
                        </pre>
                    </div>
                </div>
            )}
        </div>
    );
}
