/**
 * `ChatView` — reusable streaming chat surface.
 *
 * Owns:
 *   * messages state (user / agent / tool / pending pills),
 *   * SSE event loop (calls `streamAgentConversation`),
 *   * pending-poll lifecycle (Genie),
 *   * "Thinking" disclosure for `reasoning` events,
 *   * (optional) integrated mode picker beneath the input,
 *   * (optional) form-routing CTA driven by the `route` SSE event.
 *
 * Does NOT own:
 *   * page chrome (header, sidebar, breadcrumbs),
 *   * mode list / persona-based filtering (caller passes in
 *     `availableModes` and the controlled `mode`),
 *   * route navigation or prefill persistence (caller handles those
 *     in `onRoute`).
 *
 * Used by both the "Ask Your Data" tab and the Self Service / Home
 * surface — same component, different mode + welcome content.
 */
import { ChevronDown, ExternalLink, Send, Sparkles } from 'lucide-react';
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';

import { Button } from '../ui/button';
import { Textarea } from '../ui/textarea';
import { cn } from '../../lib/utils';
import {
    streamAgentConversation,
    type AgentChatMessage,
    type AgentEvent,
    type PendingPollEvent,
} from '../../lib/agentStream';
import { usePendingPoll } from '../../hooks/usePendingPoll';
import { ToolCallPill, type ToolCallStatus } from './ToolCallPill';
import { GenieDetailsPanel } from './GenieDetailsPanel';
import { ToolRawOutputPanel } from './ToolRawOutputPanel';
import { renderMarkdownSafe } from '../../lib/markdown';

// Each chat surface holds its own UI-side message log. Tool
// invocations and pending polls live as first-class entries here so
// the timeline reads naturally (user msg -> tool pill -> agent
// summary). They round-trip back to the backend via the stream's
// final `done` event in the form the runner expects.
type DisplayMessage =
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
         * For pending-poll handoffs (Genie) the analogous structured
         * data lives on ``genieResult`` and is rendered via
         * ``GenieDetailsPanel``.
         */
        toolResult?: unknown;
        /**
         * Raw structured payload from a completed Genie poll. Surfaces
         * the SQL, result rows, chart spec, and per-conversation deep
         * link via `<GenieDetailsPanel>`. Set on the tool pill's
         * resolution; absent until `pollResolution === 'complete'`.
         */
        genieResult?: Record<string, unknown>;
        /** When set, this tool call was a pending-poll handoff. */
        poll?: PendingPollEvent;
        pollResolution?: 'complete' | 'failed' | 'cancelled' | 'timeout';
    }
    | {
        kind: 'reasoning';
        id: string;
        text: string;
    };

export interface ChatModeOption {
    /** Mode id passed back to onModeChange and forwarded to the runner. */
    id: string;
    /** User-facing label rendered in the picker. */
    label: string;
    /** Optional small icon shown next to the label. */
    icon?: React.ReactNode;
}

export interface ChatRouteInfo {
    path: string;
    title: string;
    prefill?: Record<string, unknown> | null;
}

export interface ChatViewProps {
    /**
     * Mode forwarded to the runner as request context. The backend now runs
     * a single unified agent and ignores this, but it is still sent for
     * compatibility/logging. Defaults to "unified".
     */
    mode?: string;
    /** Optional welcome content shown when no messages yet. */
    welcomeNode?: React.ReactNode;
    /** Optional placeholder for the empty input. */
    placeholder?: string;
    /** Optional header row rendered above the messages. */
    headerActions?: React.ReactNode;
    /** Storage key for persisting messages. Disable persistence with ''. */
    storageKey?: string;
    /**
     * Optional canned prompts shown below the welcome content. Clicking one
     * auto-submits the prompt as the user's first turn so the empty state
     * doesn't feel like dead links.
     */
    samplePrompts?: string[];
    /**
     * When provided alongside `onModeChange`, ChatView renders an
     * integrated mode picker beneath the input — matching the
     * existing Self Service design and avoiding two divergent UIs
     * for the same affordance.
     */
    availableModes?: ChatModeOption[];
    onModeChange?: (modeId: string) => void;
    /**
     * Invoked when the user clicks the "Continue to form" CTA that
     * appears after a `route` SSE event. The chat surface is purely
     * presentational here; the parent owns navigation and prefill
     * persistence.
     */
    onRoute?: (route: ChatRouteInfo) => void;
    /**
     * Optional override for the route CTA's label. Useful for
     * surfaces that route to multiple form types and want a more
     * descriptive button (e.g. "View reusable assets").
     */
    formCtaLabelFor?: (path: string) => string;
    /**
     * Optional content rendered *below* the input in the empty state.
     * When provided, the empty state switches to a "landing" layout —
     * the welcome + input sit near the top (Databricks One style) and
     * this node (e.g. Data Products / Datasets rails) scrolls below it,
     * instead of the input being pinned to the bottom of the surface.
     */
    emptyStateExtras?: React.ReactNode;
    /**
     * Fired when a tool call resolves, with the tool name, its raw result
     * payload, and whether it succeeded. Lets a host surface react to agent
     * side effects — e.g. the workflow authoring panel reloads + opens the
     * draft the agent just saved. Fires once per tool result.
     */
    onToolResult?: (toolName: string, result: unknown, ok: boolean) => void;
}

/**
 * Imperative handle exposed via `forwardRef` so a parent can submit a
 * turn programmatically — for example, when an external link
 * navigates to the chat page with a pre-built query, or when a
 * discovery card click should kick off a turn instead of just filling
 * the input. Keep the surface minimal so usage stays declarative.
 */
export interface ChatViewHandle {
    /** Submit `text` as the next user turn. No-ops if streaming. */
    submitQuery: (text: string) => void;
}

const ELLIPSIS_LIMIT_MS = 60_000; // bump elapsed display past this

export const ChatView = forwardRef<ChatViewHandle, ChatViewProps>(function ChatView(
    {
        mode = 'unified',
        welcomeNode,
        placeholder = 'Ask a question...',
        headerActions,
        storageKey = `chatview_messages_${mode}`,
        samplePrompts,
        availableModes,
        onModeChange,
        onRoute,
        formCtaLabelFor,
        emptyStateExtras,
        onToolResult,
    },
    ref,
) {
    const [messages, setMessages] = useState<DisplayMessage[]>(() => {
        if (!storageKey) return [];
        if (typeof window === 'undefined') return [];
        try {
            const raw = window.localStorage.getItem(storageKey);
            if (!raw) return [];
            const parsed = JSON.parse(raw) as DisplayMessage[];
            if (!Array.isArray(parsed)) return [];
            // Drop any in-flight tool entries from a previous tab —
            // they're stale and will never resolve.
            return parsed
                .filter((m) => !(m.kind === 'tool' && m.status !== 'success' && m.status !== 'error'))
                .map((m) => (m.kind === 'reasoning' ? { ...m } : m));
        } catch {
            return [];
        }
    });
    const [statusLabel, setStatusLabel] = useState<string | null>(null);
    const [isStreaming, setIsStreaming] = useState(false);
    const [draft, setDraft] = useState('');
    const [pendingPoll, setPendingPoll] = useState<PendingPollEvent | null>(null);
    const [showThinking, setShowThinking] = useState(false);
    const [routeCta, setRouteCta] = useState<ChatRouteInfo | null>(null);
    const [showModeDropdown, setShowModeDropdown] = useState(false);
    const messagesEndRef = useRef<HTMLDivElement | null>(null);
    const abortRef = useRef<AbortController | null>(null);
    const modeDropdownRef = useRef<HTMLDivElement | null>(null);
    // Mirror of `pendingPoll` for use inside in-flight async closures
    // (e.g. submitTurn's finally block) so they always see the latest
    // value rather than the stale one captured at bind time. Without
    // this the input bar gets stuck disabled after a Genie poll
    // resolves and the continuation turn finishes streaming. Updated
    // synchronously during render to avoid any stale-effect race.
    const pendingPollRef = useRef<PendingPollEvent | null>(null);
    pendingPollRef.current = pendingPoll;

    // Map tool_call id -> tool name so we can report the resolving tool's name
    // to `onToolResult` (the tool_result event carries only the id).
    const toolNamesRef = useRef<Record<string, string>>({});

    // Whitespace-normalized signature of the most recently *applied* agent
    // message. Backs a hard guard against rendering the same bubble twice:
    // the `message` apply runs inside a deferred `setTimeout` (the tool-pill
    // gate), and at temperature 0.0 the model can re-emit byte-for-byte (or
    // whitespace-different) identical text on a follow-up iteration. The
    // in-`prev` compare can miss those, so we also short-circuit here.
    const lastAgentSigRef = useRef<string>('');

    // Persist messages so users don't lose context on refresh. Skip
    // any in-flight pills (they re-render as cancelled instead of
    // hung).
    useEffect(() => {
        if (!storageKey || typeof window === 'undefined') return;
        try {
            window.localStorage.setItem(storageKey, JSON.stringify(messages));
        } catch {
            /* storage quota / disabled — non-fatal */
        }
    }, [messages, storageKey]);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages, statusLabel]);

    // Per-second tick to drive the elapsed-time label inside running
    // tool pills. The label is computed at render time as
    // ``Date.now() - msg.startedAt``, so without a periodic re-render
    // the counter would freeze at "0s" until some other state change
    // (a tool_result, a new pill, the next message) happened to
    // re-render the row. We mount the timer only while at least one
    // pill is actively ``running``; pending-poll pills already have
    // their own per-second tick from ``usePendingPoll``.
    const [, setNowTick] = useState(0);
    const hasRunningPill = useMemo(
        () => messages.some((m) => m.kind === 'tool' && m.status === 'running'),
        [messages],
    );
    useEffect(() => {
        if (!hasRunningPill) return;
        const id = window.setInterval(() => setNowTick((n) => n + 1), 1000);
        return () => window.clearInterval(id);
    }, [hasRunningPill]);

    // Close the mode dropdown when the user clicks anywhere outside
    // it. Mirrors the same pattern Home.tsx uses so the affordance
    // feels identical between Self Service and Ask Your Data.
    useEffect(() => {
        if (!showModeDropdown) return;
        const onClick = (e: MouseEvent) => {
            if (
                modeDropdownRef.current &&
                !modeDropdownRef.current.contains(e.target as Node)
            ) {
                setShowModeDropdown(false);
            }
        };
        document.addEventListener('mousedown', onClick);
        return () => document.removeEventListener('mousedown', onClick);
    }, [showModeDropdown]);

    // Drive Genie polling whenever an active pending_poll exists.
    const pollState = usePendingPoll(pendingPoll, {
        onSettled: (final, settledEvent) => {
            // Identify which pill this resolution belongs to using the
            // *originating* pollEvent — `pendingPoll` from closure may
            // have already advanced to a newer turn (which would cause
            // us to silently mark the wrong pill, or none at all).
            const settledToolCallId = settledEvent.tool_call_id;
            const isCurrent =
                pendingPollRef.current?.tool_call_id === settledToolCallId;

            // Reflect the final state in the matching tool pill.
            setMessages((prev) =>
                prev.map((m) => {
                    if (m.kind !== 'tool' || !m.poll) return m;
                    if (m.poll.tool_call_id !== settledToolCallId) return m;
                    if (final.status === 'complete') {
                        return {
                            ...m,
                            status: 'success',
                            completedAt: Date.now(),
                            pollResolution: 'complete',
                            // Persist the structured payload so the
                            // GenieDetailsPanel can render SQL, rows,
                            // chart, and the deep link below the pill.
                            genieResult:
                                final.result && typeof final.result === 'object'
                                    ? (final.result as Record<string, unknown>)
                                    : undefined,
                        };
                    }
                    return {
                        ...m,
                        status: 'error',
                        completedAt: Date.now(),
                        errorMessage: final.error ?? final.status,
                        pollResolution:
                            final.status === 'cancelled' || final.status === 'timeout'
                                ? final.status
                                : 'failed',
                    };
                }),
            );

            // Anything below this line drives the *current* turn's
            // lifecycle (resume / error message / streaming flag /
            // pendingPoll reset). If this settle is for a stale poll
            // that's been superseded by a newer one, we only want to
            // update the visual state above and otherwise stay out of
            // the way — the new poll owns those concerns now.
            if (!isCurrent) return;

            if (final.status === 'complete') {
                // Resume the agent loop so the LLM can summarize.
                void resumeAfterPoll(final.result);
            } else if (final.status === 'failed' || final.status === 'timeout') {
                // Surface the failure as an agent message and end the turn.
                setMessages((prev) => [
                    ...prev,
                    {
                        kind: 'agent',
                        id: `${Date.now()}-pollerr`,
                        timestamp: new Date().toISOString(),
                        content: final.error
                            ? `<em>I couldn't get an answer from Genie. ${escapeHtml(final.error)}</em>`
                            : '<em>I couldn\u2019t get an answer from Genie this time.</em>',
                    },
                ]);
                setIsStreaming(false);
                setStatusLabel(null);
            } else {
                setIsStreaming(false);
                setStatusLabel(null);
            }
            setPendingPoll(null);
        },
    });

    // Walk our display messages and translate them into the wire
    // format the streaming endpoint expects. Tool pills become
    // synthetic `tool` messages once their poll has resolved so the
    // LLM can see the answer.
    //
    // Chat completion APIs require every ``role: 'tool'`` message to
    // be preceded by an ``assistant`` message whose ``tool_calls``
    // contains a matching id. Our display state never stores that
    // assistant turn (the agent runner does internally, but it isn't
    // round-tripped). So we synthesize one here, immediately before
    // each completed tool, using the arguments we captured from the
    // ``tool_call`` SSE event. Without this the upstream serving
    // endpoint returns ``HTTP 400 BAD_REQUEST`` with "messages with
    // role 'tool' must be a response to a preceding message with
    // 'tool_calls'".
    //
    // ``extras`` are appended verbatim *after* the messages-derived
    // prefix and may carry their own assistant + tool pair (the
    // resume-after-poll path does this so the just-resolved Genie
    // answer is included even if React hasn't flushed the success
    // update yet). We dedupe against extras' ``tool_call_id``s so a
    // raced flush doesn't produce duplicates.
    const buildHistory = (extras: AgentChatMessage[] = []): AgentChatMessage[] => {
        const skipToolCallIds = new Set<string>();
        for (const e of extras) {
            if (e.type === 'tool' && e.tool_call_id) skipToolCallIds.add(e.tool_call_id);
            if (e.type === 'agent' && e.tool_calls) {
                for (const tc of e.tool_calls) skipToolCallIds.add(tc.id);
            }
        }

        const out: AgentChatMessage[] = [];
        for (const m of messages) {
            if (m.kind === 'user') {
                out.push({ id: m.id, type: 'user', content: m.content, timestamp: m.timestamp });
            } else if (m.kind === 'agent') {
                out.push({
                    id: m.id,
                    type: 'agent',
                    content: m.content,
                    timestamp: m.timestamp,
                });
            } else if (m.kind === 'tool' && m.poll && m.pollResolution === 'complete') {
                if (skipToolCallIds.has(m.toolCallId)) continue;
                const completedAt = new Date(m.completedAt ?? Date.now()).toISOString();
                // Synthetic assistant turn carrying the original
                // ``tool_calls`` block so the linkage is intact.
                out.push({
                    id: `${m.id}-tc`,
                    type: 'agent',
                    content: '',
                    timestamp: completedAt,
                    tool_calls: [
                        {
                            id: m.toolCallId,
                            type: 'function',
                            function: {
                                name: m.toolName,
                                arguments: JSON.stringify(m.toolArguments ?? {}),
                            },
                        },
                    ],
                });
                // Replay the resolved Genie answer as a tool message.
                out.push({
                    id: m.id,
                    type: 'tool',
                    content: m.detail ?? `Genie answered "${m.label}"`,
                    timestamp: completedAt,
                    tool_call_id: m.toolCallId,
                    name: m.toolName,
                });
            }
        }
        return [...out, ...extras];
    };

    const submitTurn = async (
        userText: string,
        opts: {
            isContinuation?: boolean;
            /**
             * Extra wire messages appended to the history *after* the
             * messages-derived prefix. Used by the post-poll
             * continuation to send the synthetic
             * ``assistant.tool_calls`` + ``tool`` pair carrying the
             * resolved Genie answer.
             */
            toolMessages?: AgentChatMessage[];
        } = {},
    ) => {
        const controller = new AbortController();
        abortRef.current?.abort();
        abortRef.current = controller;

        setIsStreaming(true);
        setStatusLabel('Thinking...');
        setShowThinking(false);
        // New turn, new gate — stale "minimum tool-pill duration"
        // bookkeeping from a prior stream shouldn't delay the first
        // event of this one.
        eventGateRef.current = 0;
        // Scope the same-message guard to this turn only. A later,
        // unrelated turn that legitimately produces the same short reply
        // (e.g. "Done.") should still render; we only want to swallow a
        // duplicate emitted *within* a single turn.
        lastAgentSigRef.current = '';
        // Stale form-route CTAs from a prior turn shouldn't survive a
        // new question — the agent may pick a different form, or no
        // form at all. The continuation path skips this clear so the
        // CTA persists across the synthetic post-poll turn.
        if (!opts.isContinuation) {
            setRouteCta(null);
        }

        // For a normal turn we add a user bubble and stream into a
        // fresh agent slot. For a continuation (post-poll) the user
        // turn is implicit — we just stream the agent reply.
        const userMsgId = `${Date.now()}-u`;
        if (!opts.isContinuation) {
            setMessages((prev) => [
                ...prev,
                {
                    kind: 'user',
                    id: userMsgId,
                    content: userText,
                    timestamp: new Date().toISOString(),
                },
            ]);
        }

        const history = buildHistory(opts.toolMessages ?? []);

        try {
            for await (const event of streamAgentConversation(
                {
                    query: opts.isContinuation ? '' : userText,
                    conversation_history: history,
                    context: { mode },
                },
                { signal: controller.signal },
            )) {
                handleStreamEvent(event);
            }
        } catch (err) {
            if (controller.signal.aborted) {
                // User-initiated abort; surface a quiet agent line.
                setMessages((prev) => [
                    ...prev,
                    {
                        kind: 'agent',
                        id: `${Date.now()}-cancel`,
                        timestamp: new Date().toISOString(),
                        content: '<em>Cancelled.</em>',
                    },
                ]);
            } else {
                setMessages((prev) => [
                    ...prev,
                    {
                        kind: 'agent',
                        id: `${Date.now()}-err`,
                        timestamp: new Date().toISOString(),
                        content: `<em>I hit an error talking to the agent. ${escapeHtml(
                            err instanceof Error ? err.message : String(err),
                        )}</em>`,
                    },
                ]);
            }
        } finally {
            // Don't clear streaming state if a pending poll is still
            // running; the poll's onSettled will own that. Read the
            // ref (not the closure) so we see the latest state — the
            // closure value is stale once a continuation turn fires
            // after `setPendingPoll(null)`.
            if (!pendingPollRef.current) {
                setIsStreaming(false);
                setStatusLabel(null);
            }
        }
    };

    // Earliest wall-clock time at which the next agent event may
    // visually apply. Used to enforce ordering between tool pills
    // and the agent message that follows them: when a synchronous
    // tool resolves in under MIN_RUNNING_MS we delay its visible
    // settle, and we likewise delay the agent's `message` event so
    // it never renders before the pill it depends on. Without this
    // the user reported "everything pops in at once" — the message
    // would show while the pill was still spinning on fast tools.
    const eventGateRef = useRef<number>(0);

    const handleStreamEvent = (event: AgentEvent) => {
        switch (event.type) {
            case 'status': {
                const baseLabel = event.label;
                const label =
                    typeof event.elapsed_ms === 'number'
                        ? `${baseLabel} (${formatElapsed(event.elapsed_ms)})`
                        : baseLabel;
                setStatusLabel(label);
                break;
            }
            case 'tool_call': {
                setStatusLabel(event.friendly_label);
                toolNamesRef.current[event.id] = event.name;
                setMessages((prev) => [
                    ...prev,
                    {
                        kind: 'tool',
                        id: `${Date.now()}-tc-${event.id}`,
                        toolCallId: event.id,
                        toolName: event.name,
                        label: event.friendly_label,
                        detail: event.args_summary,
                        status: 'running',
                        startedAt: Date.now(),
                        toolArguments: event.arguments,
                    },
                ]);
                break;
            }
            case 'tool_result': {
                // Notify the host of the resolved tool + payload immediately
                // (independent of the pill's delayed visual settle below) so
                // it can react to agent side effects without waiting.
                onToolResult?.(toolNamesRef.current[event.id] || '', event.result, event.ok);
                // Hold the "running" pill on screen for a short
                // minimum so synchronous tools (e.g. execute_workflow,
                // metadata listings) don't flash from "Running …" to
                // "Done" in the same render frame as the agent's
                // follow-up message — the user reported "everything
                // pops in at once" before this. With the floor in
                // place even fast tools render at least one
                // perceivable "running" frame, mirroring how Genie's
                // pending-poll pill behaves.
                const MIN_RUNNING_MS = 600;
                const settle = () =>
                    setMessages((prev) =>
                        prev.map((m) => {
                            if (m.kind !== 'tool' || m.toolCallId !== event.id) return m;
                            return {
                                ...m,
                                status: event.ok ? 'success' : 'error',
                                completedAt: Date.now(),
                                errorMessage: event.error ?? undefined,
                                label: event.summary || m.label,
                                toolResult: event.result,
                            };
                        }),
                    );
                // Use a functional setter purely to read the current
                // pill state without relying on a possibly stale
                // ``messages`` closure inside this async SSE handler.
                setMessages((prev) => {
                    const pill = prev.find(
                        (m) => m.kind === 'tool' && m.toolCallId === event.id,
                    ) as Extract<DisplayMessage, { kind: 'tool' }> | undefined;
                    const elapsed = pill?.startedAt
                        ? Date.now() - pill.startedAt
                        : MIN_RUNNING_MS;
                    const delay = Math.max(0, MIN_RUNNING_MS - elapsed);
                    eventGateRef.current = Math.max(
                        eventGateRef.current,
                        Date.now() + delay,
                    );
                    if (delay === 0) {
                        // Settle synchronously inside this same updater
                        // so the gate's effect on subsequent events is
                        // immediate.
                        return prev.map((m) => {
                            if (m.kind !== 'tool' || m.toolCallId !== event.id) return m;
                            return {
                                ...m,
                                status: event.ok ? 'success' : 'error',
                                completedAt: Date.now(),
                                errorMessage: event.error ?? undefined,
                                label: event.summary || m.label,
                                toolResult: event.result,
                            };
                        });
                    }
                    setTimeout(settle, delay);
                    return prev;
                });
                break;
            }
            case 'pending_poll': {
                // Mark the matching tool entry as pending and start
                // the poll loop. The earlier 'tool_call' put it in
                // 'running' state; we transition to 'pending' so the
                // pill renders with an elapsed counter from the
                // poll hook.
                setMessages((prev) =>
                    prev.map((m) => {
                        if (m.kind !== 'tool' || m.toolCallId !== event.tool_call_id) return m;
                        return {
                            ...m,
                            status: 'pending',
                            poll: event,
                            label: event.friendly_label,
                        };
                    }),
                );
                setPendingPoll(event);
                setStatusLabel(event.friendly_label);
                break;
            }
            case 'reasoning': {
                setMessages((prev) => [
                    ...prev,
                    {
                        kind: 'reasoning',
                        id: `${Date.now()}-r`,
                        text: event.text,
                    },
                ]);
                break;
            }
            case 'message': {
                if (!event.content) break;
                const incomingSig = normalizeForDedup(event.content);
                const apply = () =>
                    setMessages((prev) => {
                        // Defensive dedup: if the runner re-emits an
                        // identical message back-to-back (we've seen this
                        // happen in the wild on certain model serving
                        // endpoints when the LLM produces the same text
                        // across two iterations — it runs at temperature
                        // 0.0, so repeats are byte-for-byte), don't push
                        // another bubble. We compare whitespace-normalized
                        // signatures so a stray trailing space / CRLF /
                        // blank-line difference can't sneak a visual dupe
                        // past us. Two guards:
                        //   1. ref signature of the last applied agent
                        //      message — covers the deferred `apply` path
                        //      where the in-`prev` compare can race.
                        //   2. the most recent agent bubble in `prev`.
                        if (incomingSig && incomingSig === lastAgentSigRef.current) {
                            return prev;
                        }
                        const lastAgent = [...prev]
                            .reverse()
                            .find((m): m is Extract<DisplayMessage, { kind: 'agent' }> => m.kind === 'agent');
                        if (lastAgent && normalizeForDedup(lastAgent.content) === incomingSig) {
                            return prev;
                        }
                        lastAgentSigRef.current = incomingSig;
                        return [
                            ...prev,
                            {
                                kind: 'agent',
                                id: `${Date.now()}-m`,
                                content: event.content,
                                timestamp: new Date().toISOString(),
                            },
                        ];
                    });
                // Honor the tool-pill gate so messages never render
                // before their preceding tool resolves visually.
                const wait = Math.max(0, eventGateRef.current - Date.now());
                if (wait === 0) {
                    apply();
                    setStatusLabel(null);
                } else {
                    setTimeout(() => {
                        apply();
                        setStatusLabel(null);
                    }, wait);
                }
                break;
            }
            case 'route': {
                // The runner extracted a `route_to_form` instruction
                // from the agent's final message. Surface it as a CTA;
                // the parent owns navigation via `onRoute`.
                setRouteCta({
                    path: event.path,
                    title: event.title,
                    prefill: event.prefill ?? null,
                });
                break;
            }
            case 'done': {
                setStatusLabel(null);
                break;
            }
            case 'error': {
                setMessages((prev) => [
                    ...prev,
                    {
                        kind: 'agent',
                        id: `${Date.now()}-streamerr`,
                        content: `<em>${escapeHtml(event.message)}</em>`,
                        timestamp: new Date().toISOString(),
                    },
                ]);
                setStatusLabel(null);
                break;
            }
            default:
                break;
        }
    };

    const resumeAfterPoll = async (result: Record<string, unknown> | null) => {
        if (!pendingPoll) return;

        // Short-circuit when Genie produced a self-contained answer.
        // The managed-MCP Genie endpoint ships its full answer
        // (markdown with headings, bullets, inline tables) on the
        // ``final_answer`` field. The LLM "summarization" turn we
        // would otherwise run on top of it adds latency, costs
        // tokens, and frequently rephrases Genie's polished output
        // into something less precise. Surfacing the markdown
        // directly in the agent bubble gives the user verbatim
        // fidelity and keeps the UX snappy. Future turns still see
        // this in conversation history (we push it as a normal
        // ``agent`` message), so the LLM has full context if the
        // user asks a follow-up.
        const finalAnswer =
            result && typeof result === 'object' && typeof result.final_answer === 'string'
                ? (result.final_answer as string).trim()
                : '';
        if (finalAnswer) {
            // Store the raw markdown — the agent message renderer
            // will run it through `renderMarkdownSafe` at view time.
            // Keeping the storage format consistent with LLM-authored
            // messages means the conversation log is always markdown
            // and we never have to detect "is this already HTML?".
            setMessages((prev) => [
                ...prev,
                {
                    kind: 'agent',
                    id: `${Date.now()}-m-genie`,
                    content: finalAnswer,
                    timestamp: new Date().toISOString(),
                },
            ]);
            setStatusLabel(null);
            setIsStreaming(false);
            setPendingPoll(null);
            return;
        }

        // Build the synthetic ``assistant.tool_calls`` + ``tool``
        // message pair carrying the resolved Genie answer back to the
        // LLM. We send BOTH messages because the streaming endpoint's
        // upstream model serving API rejects orphan tool messages
        // ("messages with role 'tool' must be a response to a
        // preceding message with 'tool_calls'"). The assistant turn
        // is reconstructed from the ``tool_call`` event we captured
        // at the start of the original turn — its ``arguments`` are
        // pulled from the matching tool display message.

        // The tool display message carrying the original arguments.
        // React may not have flushed the success-status update yet by
        // the time we run, but the arguments were stored back when
        // the ``tool_call`` SSE event arrived, so they're already on
        // the message regardless of its current status.
        const matchingTool = messages.find(
            (m): m is Extract<DisplayMessage, { kind: 'tool' }> =>
                m.kind === 'tool' && m.toolCallId === pendingPoll.tool_call_id,
        );
        const toolArgs = matchingTool?.toolArguments ?? {};

        const completedAt = new Date().toISOString();
        const baseId = `${Date.now()}`;
        const assistantToolCall: AgentChatMessage = {
            id: `${baseId}-tc`,
            type: 'agent',
            content: '',
            timestamp: completedAt,
            tool_calls: [
                {
                    id: pendingPoll.tool_call_id,
                    type: 'function',
                    function: {
                        name: pendingPoll.tool_name,
                        arguments: JSON.stringify(toolArgs),
                    },
                },
            ],
        };
        const toolMessage: AgentChatMessage = {
            id: `${baseId}-tool`,
            type: 'tool',
            content: stringifyResult(result),
            timestamp: completedAt,
            tool_call_id: pendingPoll.tool_call_id,
            name: pendingPoll.tool_name,
        };
        await submitTurn('', {
            isContinuation: true,
            toolMessages: [assistantToolCall, toolMessage],
        });
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        const text = draft.trim();
        if (!text || isStreaming || pendingPoll) return;
        setDraft('');
        await submitTurn(text);
    };

    // Expose a tiny imperative API so the parent can submit a query
    // programmatically — used by Self Service to forward `autoQuery`
    // navigation state and to wire its rich discovery cards into the
    // streaming pipeline. Refs intentionally stay narrow; if more
    // surfaces grow needs, prefer adding focused props before
    // expanding this handle.
    useImperativeHandle(
        ref,
        () => ({
            submitQuery: (text: string) => {
                const trimmed = text.trim();
                if (!trimmed || isStreaming || pendingPoll) return;
                setDraft('');
                void submitTurn(trimmed);
            },
        }),
        [isStreaming, pendingPoll],
    );

    const handleClear = () => {
        abortRef.current?.abort();
        setMessages([]);
        setPendingPoll(null);
        setStatusLabel(null);
        setIsStreaming(false);
        setRouteCta(null);
        if (storageKey && typeof window !== 'undefined') {
            try {
                window.localStorage.removeItem(storageKey);
            } catch {
                /* swallow */
            }
        }
    };

    // Live elapsed string for the active pending poll, if any.
    const pollElapsedLabel = useMemo(() => {
        if (pollState.status !== 'running') return undefined;
        return formatElapsed(pollState.elapsedMs);
    }, [pollState.elapsedMs, pollState.status]);

    const hasReasoning = useMemo(
        () => messages.some((m) => m.kind === 'reasoning'),
        [messages],
    );

    const showModePicker =
        !!availableModes && availableModes.length > 0 && !!onModeChange;

    const modePicker = showModePicker ? (
        <ModePicker
            mode={mode}
            modes={availableModes!}
            onChange={(id) => {
                onModeChange!(id);
                setShowModeDropdown(false);
            }}
            isOpen={showModeDropdown}
            onToggle={() => setShowModeDropdown((v) => !v)}
            containerRef={modeDropdownRef}
        />
    ) : null;

    const handleRouteClick = () => {
        if (!routeCta) return;
        onRoute?.(routeCta);
    };

    const routeCtaNode = routeCta ? (
        <div className="space-y-3 mt-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <div className="bg-linear-to-br from-blue-50 to-primary/5 border border-blue-200/50 rounded-2xl p-4 shadow-sm">
                <Button
                    onClick={handleRouteClick}
                    className="flex items-center gap-2 rounded-xl shadow-md hover:shadow-lg transition-all duration-200"
                >
                    <ExternalLink className="w-4 h-4" />
                    {(formCtaLabelFor ?? defaultRouteLabel)(routeCta.path)}
                </Button>
            </div>
        </div>
    ) : null;

    return (
        <div className="flex flex-col h-full">
            {headerActions && (
                <div className="flex items-center justify-between gap-3 px-1 pb-3">
                    <div className="flex-1 min-w-0">
                        {headerActions}
                    </div>
                </div>
            )}

            {/* Empty state — welcome content + initial input */}
            {messages.length === 0 && emptyStateExtras ? (
                /* Databricks One-style landing: a prominent, higher-up input
                   with rich content (Data Products / Datasets) scrolling
                   below it. */
                <div className="flex-1 overflow-y-auto custom-scrollbar">
                    <div className="max-w-3xl mx-auto w-full pt-6 px-1">
                        {welcomeNode}
                        <form onSubmit={handleSubmit} className="mt-6">
                            <div className="relative">
                                <Textarea
                                    value={draft}
                                    onChange={(e) => setDraft(e.target.value)}
                                    placeholder={placeholder}
                                    rows={3}
                                    onKeyDown={(e) => {
                                        if (e.key === 'Enter' && !e.shiftKey) {
                                            e.preventDefault();
                                            const form = e.currentTarget.closest('form');
                                            if (form && !isStreaming) form.requestSubmit();
                                        }
                                    }}
                                    className="w-full rounded-2xl border-2 border-gray-200 focus:border-primary/50 focus:ring-4 focus:ring-primary/10 transition-all duration-200 min-h-[96px] max-h-[240px] py-4 pl-5 pr-16 text-base shadow-sm"
                                />
                                <Button
                                    type="submit"
                                    disabled={isStreaming || !draft.trim()}
                                    className="absolute right-2.5 bottom-2.5 rounded-xl shadow-md hover:shadow-lg transition-all duration-200 h-10 w-10 p-0"
                                >
                                    <Send className="w-4 h-4 text-white" />
                                </Button>
                            </div>
                            {modePicker}
                        </form>
                        {samplePrompts && samplePrompts.length > 0 && (
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full mt-4">
                                {samplePrompts.map((q) => (
                                    <button
                                        key={q}
                                        type="button"
                                        onClick={() => {
                                            if (isStreaming || pendingPoll) return;
                                            setDraft('');
                                            void submitTurn(q);
                                        }}
                                        disabled={isStreaming || !!pendingPoll}
                                        className="relative p-2.5 rounded-xl border border-gray-200 hover:shadow-md hover:bg-white/80 hover:border-primary/50 transition-all duration-200 text-left group disabled:opacity-50 disabled:cursor-not-allowed"
                                    >
                                        <div className="text-[13px] font-medium text-gray-900 group-hover:text-primary transition-colors">
                                            {q}
                                        </div>
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                    <div className="mt-10">{emptyStateExtras}</div>
                </div>
            ) : messages.length === 0 ? (
                <div className="flex-1 flex flex-col">
                    {/* Welcome + sample prompts hug each other at the
                        top; a flex-1 spacer below pushes the input form
                        to the bottom. Previously a flex-1 on the welcome
                        wrapper stretched the gap so the prompts floated
                        next to the input — that read worse than having
                        them sit under the description. */}
                    {welcomeNode}
                    {samplePrompts && samplePrompts.length > 0 && (
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-w-2xl w-full mx-auto mt-4">
                            {samplePrompts.map((q) => (
                                <button
                                    key={q}
                                    type="button"
                                    onClick={() => {
                                        if (isStreaming || pendingPoll) return;
                                        setDraft('');
                                        void submitTurn(q);
                                    }}
                                    disabled={isStreaming || !!pendingPoll}
                                    className="relative p-2.5 rounded-xl border border-gray-200 hover:shadow-md hover:bg-white/80 hover:border-primary/50 transition-all duration-200 text-left group disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    <div className="text-[13px] font-medium text-gray-900 group-hover:text-primary transition-colors">
                                        {q}
                                    </div>
                                </button>
                            ))}
                        </div>
                    )}
                    <div className="flex-1" />
                    <form onSubmit={handleSubmit}>
                        <div className="flex gap-2 items-center">
                            <Textarea
                                value={draft}
                                onChange={(e) => setDraft(e.target.value)}
                                placeholder={placeholder}
                                rows={1}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' && !e.shiftKey) {
                                        e.preventDefault();
                                        const form = e.currentTarget.closest('form');
                                        if (form && !isStreaming) form.requestSubmit();
                                    }
                                }}
                                className="flex-1 rounded-xl border-2 border-gray-200 focus:border-primary/50 focus:ring-4 focus:ring-primary/10 transition-all duration-200 min-h-[48px] max-h-[200px] py-3"
                            />
                            <Button
                                type="submit"
                                disabled={isStreaming || !draft.trim()}
                                className="rounded-xl shadow-md hover:shadow-lg transition-all duration-200 h-10"
                            >
                                <Send className="w-4 h-4 text-white" />
                            </Button>
                            {/* No "New Chat" button in the empty state -
                                there's nothing to clear yet. Mirrors Home.tsx
                                where the button only appears once a
                                conversation exists. */}
                        </div>
                        {modePicker}
                    </form>
                </div>
            ) : (
                <>
                    {/* Messages */}
                    {/* Messages — consecutive tool pills are grouped into
                        a single collapsible block to keep the column
                        compact. The most recent step is always shown;
                        earlier steps reveal on demand. */}
                    <div className="flex-1 overflow-y-auto space-y-4 pr-2 custom-scrollbar">
                        {renderMessages(messages, pollElapsedLabel, pendingPoll?.tool_call_id)}

                        {/* Live Genie progress as it works. Genie keeps its
                            final answer empty until the end and streams its
                            working steps instead, so we show a compact, muted
                            "what it's doing now" feed (replaced each poll — the
                            step list revises non-additively). It's superseded by
                            the real answer once the poll settles. */}
                        {pollState.status === 'running' && pollState.partialAnswer && (
                            <div className="flex justify-start animate-in fade-in duration-200">
                                <div className="max-w-[80%] rounded-2xl px-4 py-3 bg-gray-50/70 border border-gray-200/50">
                                    <div className="flex items-center gap-1.5 mb-1.5 text-[11px] font-medium text-gray-500">
                                        <span className="relative flex h-1.5 w-1.5">
                                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
                                            <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-blue-500" />
                                        </span>
                                        Genie is working…
                                    </div>
                                    <div className="text-xs leading-relaxed text-gray-500 whitespace-pre-line">
                                        {pollState.partialAnswer}
                                    </div>
                                </div>
                            </div>
                        )}

                        {hasReasoning && (
                            <div className="self-start">
                                <button
                                    type="button"
                                    onClick={() => setShowThinking((prev) => !prev)}
                                    className="inline-flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-800"
                                >
                                    <Sparkles className="w-3 h-3" />
                                    {showThinking ? 'Hide' : 'Show'} reasoning
                                    <ChevronDown
                                        className={cn(
                                            'w-3 h-3 transition-transform',
                                            showThinking && 'rotate-180',
                                        )}
                                    />
                                </button>
                                {showThinking && (
                                    <div className="mt-2 max-w-[80%] text-xs text-gray-600 bg-gray-50 border border-gray-200 rounded-xl p-3 whitespace-pre-wrap">
                                        {messages
                                            .filter((m) => m.kind === 'reasoning')
                                            .map((m) => (m as { text: string }).text)
                                            .join('\n\n')}
                                    </div>
                                )}
                            </div>
                        )}

                        {statusLabel && (
                            <div className="flex items-center gap-2 text-xs text-gray-500 italic">
                                <span className="inline-block w-1.5 h-1.5 bg-gray-400 rounded-full animate-pulse" />
                                {statusLabel}
                            </div>
                        )}

                        {routeCtaNode}

                        <div ref={messagesEndRef} />
                    </div>

                    {/* Input — "New Chat" sits inline with Send to mirror
                        the Self Service / Home view. Single, consistent
                        affordance instead of a separate top-right link. */}
                    <form onSubmit={handleSubmit} className="border-t border-gray-200/50 pt-4 mt-4">
                        <div className="flex gap-2 items-center">
                            <Textarea
                                value={draft}
                                onChange={(e) => setDraft(e.target.value)}
                                placeholder={placeholder}
                                rows={1}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' && !e.shiftKey) {
                                        e.preventDefault();
                                        const form = e.currentTarget.closest('form');
                                        if (form && !isStreaming && !pendingPoll) {
                                            form.requestSubmit();
                                        }
                                    }
                                }}
                                className="flex-1 rounded-xl border-2 border-gray-200 focus:border-primary/50 focus:ring-4 focus:ring-primary/10 transition-all duration-200 min-h-[48px] max-h-[200px] py-3"
                            />
                            <Button
                                type="submit"
                                disabled={isStreaming || !!pendingPoll || !draft.trim()}
                                className="rounded-xl shadow-md hover:shadow-lg transition-all duration-200 h-10"
                            >
                                <Send className="w-4 h-4 text-white" />
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={handleClear}
                                className="rounded-xl border-primary/30 text-primary hover:bg-primary/5 hover:text-primary/80 transition-all duration-200 h-10 px-4 text-[10px] font-bold uppercase tracking-wider whitespace-nowrap"
                            >
                                New Chat
                            </Button>
                        </div>
                        {modePicker}
                    </form>
                </>
            )}
        </div>
    );
});

function MessageRow({
    msg,
    pollElapsedLabel,
    activePollToolCallId,
}: {
    msg: DisplayMessage;
    pollElapsedLabel?: string;
    /**
     * `tool_call_id` of the pending poll that owns the live elapsed
     * timer. We only attach `pollElapsedLabel` to the pill whose poll
     * matches — otherwise a stale pill still in `pending` (because
     * its onSettled never reached it) would incorrectly inherit the
     * new turn's clock.
     */
    activePollToolCallId?: string;
}) {
    if (msg.kind === 'user') {
        return (
            <div className="flex justify-end animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div className="max-w-[80%] rounded-2xl px-4 py-3 shadow-sm bg-primary text-white">
                    <p className="text-sm leading-relaxed whitespace-pre-wrap">{msg.content}</p>
                </div>
            </div>
        );
    }
    if (msg.kind === 'agent') {
        // Agent messages are stored as markdown (Genie's `final_answer`
        // ships as markdown; the LLM is now prompted to produce
        // markdown too). We render to HTML at view time via
        // `renderMarkdownSafe` so the `prose` class can style
        // headings, tables, code blocks, etc. consistently. Legacy
        // persisted HTML content passes through marked unchanged.
        // The `agent-prose` class layers on chat-appropriate spacing
        // and heading sizes (the typography plugin's defaults are
        // calibrated for a blog post and look obnoxiously large
        // inside a chat bubble).
        const html = renderMarkdownSafe(msg.content);
        return (
            <div className="flex justify-start animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div className="max-w-[80%] rounded-2xl px-4 py-3 shadow-sm bg-gray-50 text-gray-900 border border-gray-200/50">
                    <div
                        className="text-sm leading-relaxed prose prose-sm agent-prose max-w-none [&_a]:text-blue-600 [&_a]:underline [&_a]:underline-offset-2 [&_a:hover]:text-blue-700"
                        dangerouslySetInnerHTML={{ __html: html }}
                    />
                </div>
            </div>
        );
    }
    if (msg.kind === 'tool') {
        const isLivePending =
            msg.status === 'pending' &&
            !!activePollToolCallId &&
            msg.poll?.tool_call_id === activePollToolCallId;
        const elapsed =
            isLivePending && pollElapsedLabel
                ? pollElapsedLabel
                : msg.status === 'running' && msg.startedAt
                    ? formatElapsed(Date.now() - msg.startedAt)
                    : msg.status === 'pending' && msg.startedAt
                        ? // Stale pending pill (its poll was superseded
                          // before settling) — show its own frozen-ish
                          // elapsed instead of the new turn's clock.
                          formatElapsed(Date.now() - msg.startedAt)
                        : (msg.status === 'success' || msg.status === 'error') &&
                            msg.startedAt && msg.completedAt
                            ? // Settled pill — keep the total runtime
                              // visible so the user can see "this tool
                              // took 8s" at a glance even after it's
                              // resolved. Without this the counter
                              // disappeared the moment the pill turned
                              // green.
                              formatElapsed(msg.completedAt - msg.startedAt)
                            : undefined;
        // Only Genie tool calls carry a structured payload worth
        // surfacing in a panel today. Other tools (catalog lookups,
        // etc.) are short-lived and their detail string suffices.
        const isGenie =
            msg.toolName === 'ask_your_data' ||
            msg.toolName?.startsWith('genie') ||
            !!msg.genieResult;
        // Synchronous tools: surface the raw result in a collapsible
        // expander so SAs can verify exactly what data the agent saw.
        // Skip for Genie — its dedicated panel renders SQL, results,
        // and a chart and the raw payload would be redundant. Skip
        // when there's nothing to show (errors that didn't return a
        // body, in-flight pills, etc.).
        const showRawOutput =
            !isGenie &&
            (msg.status === 'success' || msg.status === 'error') &&
            msg.toolResult !== undefined &&
            msg.toolResult !== null;
        return (
            <div className="flex flex-col items-start gap-0 min-w-0 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <ToolCallPill
                    label={msg.label}
                    status={msg.status}
                    detail={msg.detail}
                    errorMessage={msg.errorMessage}
                    elapsedLabel={elapsed}
                />
                {isGenie && msg.pollResolution === 'complete' && (
                    // Wider than the message bubble (80%) — SQL and
                    // result tables read much better with full chat-
                    // column width.
                    <div className="w-full">
                        <GenieDetailsPanel result={msg.genieResult} />
                    </div>
                )}
                {showRawOutput && (
                    <ToolRawOutputPanel
                        toolName={msg.toolName}
                        toolArguments={msg.toolArguments}
                        result={msg.toolResult}
                    />
                )}
            </div>
        );
    }
    return null;
}

/**
 * Render a flat list of `DisplayMessage`s, batching consecutive tool
 * pills into a `ToolCallGroup` so a long chain of metadata calls
 * doesn't dominate the chat column. Non-tool messages render
 * one-per-row exactly as before.
 */
function renderMessages(
    messages: DisplayMessage[],
    pollElapsedLabel?: string,
    activePollToolCallId?: string,
): React.ReactNode {
    const out: React.ReactNode[] = [];
    let i = 0;
    while (i < messages.length) {
        const m = messages[i];
        if (m.kind === 'tool') {
            const start = i;
            while (i < messages.length && messages[i].kind === 'tool') i++;
            const group = messages.slice(start, i) as Extract<DisplayMessage, { kind: 'tool' }>[];
            out.push(
                <ToolCallGroup
                    key={`group-${group[0].id}`}
                    items={group}
                    pollElapsedLabel={pollElapsedLabel}
                    activePollToolCallId={activePollToolCallId}
                />,
            );
            continue;
        }
        out.push(
            <MessageRow
                key={m.id}
                msg={m}
                pollElapsedLabel={pollElapsedLabel}
                activePollToolCallId={activePollToolCallId}
            />,
        );
        i++;
    }
    return out;
}

/**
 * Renders a run of consecutive tool calls. By default only the most
 * recent pill is visible; a small toggle reveals the earlier steps.
 * If a step is currently `running` (or `pending`) we keep it visible
 * regardless so the user can see the live activity.
 */
function ToolCallGroup({
    items,
    pollElapsedLabel,
    activePollToolCallId,
}: {
    items: Extract<DisplayMessage, { kind: 'tool' }>[];
    pollElapsedLabel?: string;
    activePollToolCallId?: string;
}) {
    const [expanded, setExpanded] = useState(false);

    if (items.length === 1) {
        return (
            <MessageRow
                msg={items[0]}
                pollElapsedLabel={pollElapsedLabel}
                activePollToolCallId={activePollToolCallId}
            />
        );
    }

    const last = items[items.length - 1];
    const earlier = items.slice(0, -1);
    // If something in the earlier batch is still mid-flight, keep it
    // visible — hiding an in-progress spinner is confusing.
    const hasRunningEarlier = earlier.some(
        (m) => m.status === 'running' || m.status === 'pending',
    );
    const showEarlier = expanded || hasRunningEarlier;

    return (
        <div className="flex flex-col gap-1.5 min-w-0 animate-in fade-in slide-in-from-bottom-2 duration-300">
            {showEarlier &&
                earlier.map((m) => (
                    <MessageRow
                        key={m.id}
                        msg={m}
                        pollElapsedLabel={pollElapsedLabel}
                        activePollToolCallId={activePollToolCallId}
                    />
                ))}
            <MessageRow
                msg={last}
                pollElapsedLabel={pollElapsedLabel}
                activePollToolCallId={activePollToolCallId}
            />
            {!hasRunningEarlier && (
                <button
                    type="button"
                    onClick={() => setExpanded((prev) => !prev)}
                    className="self-start inline-flex items-center gap-1 text-[11px] text-gray-500 hover:text-gray-800 transition-colors ml-3"
                >
                    <ChevronDown
                        className={cn(
                            'w-3 h-3 transition-transform',
                            expanded && 'rotate-180',
                        )}
                    />
                    {expanded
                        ? 'Hide earlier steps'
                        : `Show ${earlier.length} earlier step${earlier.length === 1 ? '' : 's'}`}
                </button>
            )}
        </div>
    );
}

function formatElapsed(ms: number): string {
    if (ms < ELLIPSIS_LIMIT_MS) {
        return `${Math.max(0, Math.round(ms / 1000))}s`;
    }
    const minutes = Math.floor(ms / 60_000);
    const seconds = Math.round((ms % 60_000) / 1000);
    return seconds === 0 ? `${minutes}m` : `${minutes}m ${seconds}s`;
}

function escapeHtml(s: string): string {
    return s
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Whitespace-insensitive signature for dedup comparisons. Collapses all
// runs of whitespace (including the blank lines markdown introduces) to a
// single space so two visually identical agent replies that differ only by
// trailing spaces / CRLFs / blank-line count are treated as the same.
function normalizeForDedup(s: string): string {
    return s.replace(/\s+/g, ' ').trim();
}

function stringifyResult(result: Record<string, unknown> | null): string {
    if (result === null || result === undefined) return '<no result>';
    try {
        return JSON.stringify(result, null, 2);
    } catch {
        return String(result);
    }
}

/** Default label for the route CTA when the parent doesn't override. */
function defaultRouteLabel(path: string): string {
    if (!path) return 'Continue to form';
    if (path.startsWith('/paas/') || path.startsWith('/daas/')) {
        return 'Go to pre-filled form';
    }
    if (path.includes('/community/links') || path === '/community-links') {
        return 'Go to community links';
    }
    if (path.includes('/community/assets') || path === '/reusable-assets') {
        return 'View reusable assets';
    }
    if (path.includes('/community/training') || path === '/training') {
        return 'Go to training';
    }
    if (path.includes('/community/events') || path === '/events') {
        return 'View events';
    }
    const parts = path.split('/').filter(Boolean);
    if (parts.length === 0) return 'Continue';
    const last = parts[parts.length - 1];
    return `Go to ${last.replace(/-/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}`;
}

interface ModePickerProps {
    mode: string;
    modes: ChatModeOption[];
    onChange: (id: string) => void;
    isOpen: boolean;
    onToggle: () => void;
    containerRef: React.RefObject<HTMLDivElement | null>;
}

/**
 * Compact mode picker rendered beneath the input bar. Shared by the
 * Self Service / Governance / FinOps surfaces and the Ask Your Data
 * surface so users see a single, consistent affordance for switching
 * agents — there's no longer a separate per-page implementation.
 */
function ModePicker({
    mode,
    modes,
    onChange,
    isOpen,
    onToggle,
    containerRef,
}: ModePickerProps) {
    const current = modes.find((m) => m.id === mode);
    return (
        <div className="mt-2 flex items-center gap-2 px-1 relative" ref={containerRef}>
            <button
                type="button"
                onClick={onToggle}
                className="flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-gray-900 transition-colors duration-200 py-1"
            >
                <span className="flex items-center gap-1.5">
                    {current?.icon}
                    {current?.label ?? mode}
                </span>
                <ChevronDown
                    className={cn(
                        'w-3 h-3 transition-transform duration-200',
                        isOpen && 'rotate-180',
                    )}
                />
            </button>
            {isOpen && (
                <div className="absolute bottom-full left-0 mb-1 w-48 bg-white rounded-xl shadow-xl border border-gray-200 py-1.5 z-50 animate-in fade-in slide-in-from-bottom-2 duration-200">
                    {modes.map((m) => {
                        const isSelected = m.id === mode;
                        return (
                            <button
                                key={m.id}
                                type="button"
                                onClick={() => onChange(m.id)}
                                className={cn(
                                    'w-full flex items-center gap-2.5 px-3 py-2 text-xs transition-colors duration-200',
                                    isSelected
                                        ? 'bg-primary/5 text-primary font-semibold'
                                        : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900',
                                )}
                            >
                                <span className={isSelected ? 'text-primary' : 'text-gray-400'}>
                                    {m.icon}
                                </span>
                                {m.label}
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
