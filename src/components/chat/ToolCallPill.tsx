/**
 * `ToolCallPill` — small visual chip that renders a single tool
 * invocation's lifecycle (running -> success / error). Used inside
 * `ChatView` to make agent activity visible in real time.
 *
 * The optional `detail` (e.g. summarized args) is rendered INLINE
 * inside the chip so a row of tool calls stays compact. For errors
 * we keep `errorMessage` on a second line because it tends to be
 * longer and more important than the args summary.
 */
import { Check, AlertCircle, Loader2, Wrench } from 'lucide-react';
import { cn } from '../../lib/utils';

export type ToolCallStatus = 'running' | 'success' | 'error' | 'pending';

export interface ToolCallPillProps {
    label: string;
    status: ToolCallStatus;
    /** Short args summary, rendered inline next to the label. */
    detail?: string;
    /** Optional time-since-started render, e.g. "12s". */
    elapsedLabel?: string;
    /** Error message rendered below the chip when status === 'error'. */
    errorMessage?: string;
}

export function ToolCallPill({ label, status, detail, elapsedLabel, errorMessage }: ToolCallPillProps) {
    const Icon =
        status === 'running' || status === 'pending'
            ? Loader2
            : status === 'success'
                ? Check
                : status === 'error'
                    ? AlertCircle
                    : Wrench;

    return (
        <div className="flex flex-col gap-1 min-w-0 max-w-full">
            <div
                className={cn(
                    // max-w/min-w-0 so the inline detail can truncate without
                    // pushing the chip wider than the chat column.
                    'inline-flex items-center gap-2 self-start rounded-full px-3 py-1 text-xs font-medium border max-w-full min-w-0',
                    status === 'running' || status === 'pending'
                        ? 'bg-blue-50 text-blue-700 border-blue-200'
                        : status === 'success'
                            ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                            : status === 'error'
                                ? 'bg-red-50 text-red-700 border-red-200'
                                : 'bg-gray-50 text-gray-700 border-gray-200',
                )}
            >
                <Icon
                    className={cn(
                        'w-3.5 h-3.5 shrink-0',
                        (status === 'running' || status === 'pending') && 'animate-spin',
                    )}
                />
                <span className="shrink-0">{label}</span>
                {detail && (
                    <span
                        className="opacity-70 font-normal truncate min-w-0"
                        title={detail}
                    >
                        · {detail}
                    </span>
                )}
                {elapsedLabel && (
                    <span className="text-[10px] uppercase tracking-wider opacity-70 shrink-0">
                        {elapsedLabel}
                    </span>
                )}
            </div>
            {errorMessage && (
                <p className="text-xs text-red-600 ml-3 line-clamp-2">{errorMessage}</p>
            )}
        </div>
    );
}
