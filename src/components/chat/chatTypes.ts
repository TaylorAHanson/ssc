/**
 * Shared chat types, extracted from `ChatView` so the chat *session store*
 * (`src/stores/chatSessionStore.ts`) and the view can both reference them
 * without a circular import.
 */
import type { ChartEncoding, Dataset } from '../../lib/charting';
import type { ToolCallStatus } from './ToolCallPill';

export interface ChatChart {
    dataset?: Dataset;
    spec?: Record<string, unknown>;
    encoding?: Partial<ChartEncoding>;
}

// Each chat surface holds its own UI-side message log. Tool invocations
// live as first-class entries here so the timeline reads
// naturally (user msg -> tool pill -> agent summary). They round-trip back to
// the backend via the stream's final `done` event in the form the runner
// expects.
export type DisplayMessage =
    | {
        kind: 'user';
        id: string;
        content: string;
        timestamp: string;
    }
    | {
        kind: 'agent';
        id: string;
        content: string;
        timestamp: string;
        /** Next-step replies offered as chips; shown only on the latest message. */
        suggestions?: string[];
        /** The turn's one chart, drawn under the final answer it supports. */
        chart?: ChatChart;
    }
    | {
        kind: 'tool';
        id: string;
        toolCallId: string;
        toolName: string;
        label: string;
        detail?: string;
        status: ToolCallStatus;
        startedAt: number;
        completedAt?: number;
        errorMessage?: string;
        /**
         * Raw arguments the LLM produced for the call. Persisted so we
         * can synthesize the matching ``assistant.tool_calls`` block
         * when replaying this tool result on a continuation turn —
         * without it the model serving endpoint rejects the request
         * with ``role 'tool' must be a response to a preceding
         * message with 'tool_calls'``.
         */
        toolArguments?: Record<string, unknown>;
        /**
         * Raw, JSON-serializable payload the tool returned. Captured
         * from the ``tool_result`` SSE event for synchronous tools so
         * the UI can render a "Raw output" expander under every pill.
         */
        toolResult?: unknown;
        /** Set when the backend withheld the result (a tool marked ``hide_result``). */
        resultHidden?: boolean;
        /**
         * Resolved chart payload for tools that produce a chart (the
         * ``render_chart`` agent tool, or any tool returning tabular data /
         * a Vega-Lite spec). Bound at event-handle time: when the tool asks
         * to re-graph the last answer, the dataset is filled in from the
         * conversation's most recent tabular result so the chart is
         * self-contained for rendering.
         */
        chart?: ChatChart;
    }
    | {
        kind: 'reasoning';
        id: string;
        text: string;
    };

export interface ChatRouteInfo {
    path: string;
    title: string;
}
