/**
 * Client for the streaming agent endpoint.
 *
 * The browser's native EventSource doesn't support POST + custom
 * headers, which we need for both the dev role override header and
 * the future per-page mode selection. This module wraps `fetch` +
 * `ReadableStream` to parse a Server-Sent Events response into
 * typed JS events and exposes a single async generator
 * (`streamAgentConversation`) that the chat UI can consume.
 *
 * Wire format mirrors `backend/app/agents/events.py`. New event types
 * are forwarded verbatim with `type: 'unknown'` so a backend that
 * adds new events doesn't break older frontends.
 */
import { useUserStore } from '../stores/userStore';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

// ─── Event shapes (mirror app/agents/events.py) ──────────────────────

export interface StatusEvent {
    type: 'status';
    label: string;
    elapsed_ms?: number;
}

export interface ToolCallEvent {
    type: 'tool_call';
    id: string;
    name: string;
    friendly_label: string;
    args_summary?: string;
    /**
     * Raw arguments the LLM produced for the call. The UI keeps a copy
     * so it can synthesize the matching `assistant.tool_calls` block
     * when replaying a tool result on a continuation turn — without
     * that block, the model serving endpoint rejects the request with
     * "messages with role 'tool' must be a response to a preceding
     * message with 'tool_calls'".
     */
    arguments?: Record<string, unknown>;
}

export interface ToolResultEvent {
    type: 'tool_result';
    id: string;
    name: string;
    ok: boolean;
    summary?: string;
    error?: string;
    /**
     * Raw, JSON-serializable payload the tool returned. Surfaced
     * verbatim in a collapsible "Raw output" panel under the pill so
     * an SA can inspect exactly what the agent saw.
     */
    result?: unknown;
}

export interface PendingPollEvent {
    type: 'pending_poll';
    kind: string;
    ids: Record<string, unknown>;
    friendly_label: string;
    tool_call_id: string;
    tool_name: string;
}

export interface ReasoningEvent {
    type: 'reasoning';
    text: string;
}

export interface MessageEvent {
    type: 'message';
    content: string;
}

export interface RouteEvent {
    type: 'route';
    path: string;
    title: string;
    prefill?: Record<string, unknown> | null;
}

export interface DoneEvent {
    type: 'done';
    messages?: Array<Record<string, unknown>>;
}

export interface ErrorEvent {
    type: 'error';
    message: string;
    fatal?: boolean;
}

export interface UnknownEvent {
    type: 'unknown';
    raw: Record<string, unknown>;
}

export type AgentEvent =
    | StatusEvent
    | ToolCallEvent
    | ToolResultEvent
    | PendingPollEvent
    | ReasoningEvent
    | MessageEvent
    | RouteEvent
    | DoneEvent
    | ErrorEvent
    | UnknownEvent;

// ─── Wire types for the streaming endpoint ───────────────────────────

export interface AgentChatToolCall {
    id: string;
    type: 'function';
    function: {
        name: string;
        /** JSON-encoded arguments — chat completion APIs expect a string here. */
        arguments: string;
    };
}

export interface AgentChatMessage {
    id: string;
    /** 'user' | 'agent' | 'tool' — 'tool' is used to replay a resolved
     *  pending-poll back to the runner as a synthetic tool message. */
    type: 'user' | 'agent' | 'tool';
    content: string;
    /** ISO timestamp. */
    timestamp: string;
    /** For 'tool' messages only — keeps linkage to the assistant turn. */
    tool_call_id?: string;
    /** For 'tool' messages only — the original tool name. */
    name?: string;
    /**
     * For 'agent' (assistant) messages only. Set when the assistant
     * turn was a tool-call announcement. The UI synthesizes one of
     * these immediately before each replayed tool message so the
     * model serving endpoint sees the required
     * ``user → assistant(tool_calls) → tool`` linkage.
     */
    tool_calls?: AgentChatToolCall[];
}

export interface StreamConversationRequest {
    query: string;
    conversation_history?: AgentChatMessage[];
    context?: Record<string, unknown>;
}

export interface StreamConversationOptions {
    /** Aborts the underlying fetch when triggered. */
    signal?: AbortSignal;
}

// ─── Public API ──────────────────────────────────────────────────────

function authHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
    };
    const { isDevMode, activeRoleOverride } = useUserStore.getState();
    if (isDevMode && activeRoleOverride) {
        headers['X-Dev-Role-Override'] = activeRoleOverride;
    }
    return headers;
}

/**
 * Stream an agent conversation turn. Yields parsed events as the
 * server emits them; terminates after `done` or a fatal `error`.
 *
 * The caller owns rendering each event into the chat UI. See
 * `ChatView.tsx` for the canonical consumer.
 */
export async function* streamAgentConversation(
    request: StreamConversationRequest,
    options: StreamConversationOptions = {},
): AsyncGenerator<AgentEvent, void, unknown> {
    const response = await fetch(`${API_BASE_URL}/agent/conversation/stream`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify(request),
        signal: options.signal,
    });

    if (!response.ok) {
        const text = await response.text().catch(() => response.statusText);
        throw new Error(`Stream failed: HTTP ${response.status} ${text}`);
    }

    if (!response.body) {
        throw new Error('Stream response has no body.');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
        // SSE frames are separated by a blank line ("\n\n"). We hold
        // partial frames in `buffer` and yield each complete one as
        // we see it.
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            let separatorIdx = buffer.indexOf('\n\n');
            while (separatorIdx !== -1) {
                const frame = buffer.slice(0, separatorIdx);
                buffer = buffer.slice(separatorIdx + 2);
                const event = parseFrame(frame);
                if (event) {
                    yield event;
                    if (event.type === 'done' || (event.type === 'error' && event.fatal)) {
                        return;
                    }
                }
                separatorIdx = buffer.indexOf('\n\n');
            }
        }

        // Flush any trailing frame on EOF (shouldn't happen for a
        // well-formed server, but be defensive).
        const trailing = buffer.trim();
        if (trailing) {
            const event = parseFrame(trailing);
            if (event) yield event;
        }
    } finally {
        // Best-effort cancel so the server can stop work if we exit
        // early (e.g. unmount, abort).
        try { await reader.cancel(); } catch { /* swallow */ }
    }
}

function parseFrame(frame: string): AgentEvent | null {
    let eventType: string | null = null;
    const dataLines: string[] = [];
    for (const rawLine of frame.split('\n')) {
        const line = rawLine.replace(/\r$/, '');
        if (!line || line.startsWith(':')) continue;
        if (line.startsWith('event:')) {
            eventType = line.slice('event:'.length).trim();
        } else if (line.startsWith('data:')) {
            dataLines.push(line.slice('data:'.length).replace(/^\s/, ''));
        }
    }
    if (dataLines.length === 0) return null;
    let parsed: Record<string, unknown>;
    try {
        parsed = JSON.parse(dataLines.join('\n'));
    } catch {
        return null;
    }
    const type = (parsed.type as string) || eventType || '';
    switch (type) {
        case 'status':
        case 'tool_call':
        case 'tool_result':
        case 'pending_poll':
        case 'reasoning':
        case 'message':
        case 'route':
        case 'done':
        case 'error':
            return { ...(parsed as object), type } as AgentEvent;
        default:
            return { type: 'unknown', raw: parsed };
    }
}

// ─── Genie poll endpoint helpers ─────────────────────────────────────

export type GeniePollStatus = 'running' | 'complete' | 'failed';

export interface GeniePollRequest {
    /**
     * Optional Genie Space ID echoed from the pending_poll event. Empty /
     * absent means general Databricks Genie (the new "Databricks One"
     * chat) — searches across the caller's accessible UC data + spaces.
     */
    space_id?: string;
    conversation_id: string;
    message_id: string;
    question?: string;
}

export interface GeniePollResponse {
    status: GeniePollStatus;
    result?: Record<string, unknown> | null;
    error?: string | null;
    attempt_after_ms?: number | null;
}

export async function pollGenieResponse(
    body: GeniePollRequest,
    options: { signal?: AbortSignal } = {},
): Promise<GeniePollResponse> {
    const response = await fetch(`${API_BASE_URL}/agent/poll/genie`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...(useUserStore.getState().isDevMode &&
            useUserStore.getState().activeRoleOverride
                ? { 'X-Dev-Role-Override': useUserStore.getState().activeRoleOverride as string }
                : {}),
        },
        body: JSON.stringify(body),
        signal: options.signal,
    });
    if (!response.ok) {
        const text = await response.text().catch(() => response.statusText);
        throw new Error(`Poll failed: HTTP ${response.status} ${text}`);
    }
    return response.json();
}
