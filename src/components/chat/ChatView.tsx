/**
 * `ChatView` — reusable streaming chat surface.
 *
 * Owns:
 *   * messages state (user / agent / tool pills),
 *   * SSE event loop (calls `streamAgentConversation`),
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
 * Used by the agent page and the in-page workflow authoring assistant —
 * same component, different mode + welcome content.
 */
import { ChevronDown, Download, ExternalLink, Send, Sparkles, Square } from 'lucide-react';
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';

import { Button } from '../ui/button';
import { Textarea } from '../ui/textarea';
import { cn } from '../../lib/utils';
import {
    streamAgentConversation,
    type AgentChatMessage,
    type AgentEvent,
} from '../../lib/agentStream';
import { getSessionId } from '../../lib/chatPersistence';
import { useBrandingStore } from '../../stores/brandingStore';
import { useChatSession } from '../../stores/chatSessionStore';
import type { ChatChart, ChatRouteInfo, DisplayMessage } from './chatTypes';
import { downloadTranscript } from './transcript';
import { NextStepSuggestions, PromptChip } from './PromptChip';
import { sanitizeSuggestions, splitSuggestions } from './suggestions';
import { ToolCallPill } from './ToolCallPill';
import { ToolRawOutputPanel } from './ToolRawOutputPanel';
import { ChartPanel } from './ChartPanel';
import { VegaLiteChart } from './VegaLiteChart';
import { chartCandidate, findLastDataset } from './toolChart';
import { SqlResultPanel } from './SqlResultPanel';
import { asSqlResult } from './sqlResult';
import { renderMarkdownSafe } from '../../lib/markdown';
import { errorMessage } from '../../lib/errors';

export interface ChatModeOption {
    /** Mode id passed back to onModeChange and forwarded to the runner. */
    id: string;
    /** User-facing label rendered in the picker. */
    label: string;
    /** Optional small icon shown next to the label. */
    icon?: React.ReactNode;
}

// `ChatRouteInfo` and `DisplayMessage` now live in `./chatTypes` (shared with
// the chat session store); re-exported here so existing importers of ChatView
// (e.g. Home.tsx) keep resolving the type from this module.
export type { ChatRouteInfo, DisplayMessage } from './chatTypes';

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
     * Pass `null` for that landing layout with nothing below the input.
     */
    emptyStateExtras?: React.ReactNode;
    /**
     * Fired when a tool call resolves, with the tool name, its raw result
     * payload, whether it succeeded, and the raw arguments the agent passed to
     * the call. Lets a host surface react to agent side effects — e.g. the
     * workflow authoring panel hydrates the editor with the drafted `graph_spec`
     * (which lives in the call's arguments) and opens the saved draft. Fires
     * once per tool result.
     */
    onToolResult?: (
        toolName: string,
        result: unknown,
        ok: boolean,
        args?: Record<string, unknown>,
    ) => void;
    /**
     * Extra request context merged into every turn's `context` alongside `mode`.
     * Read fresh at submit time (not captured at mount), so a host can pass live
     * page state — e.g. the workflow authoring studio sends the draft currently
     * open in the editor, including unsaved edits, so the agent works from what
     * the admin sees instead of the saved database copy.
     */
    extraContext?: () => Record<string, unknown>;
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
const STOP_NUDGE_MS = 20_000;
const STOP_NUDGE_TOOL_CALLS = 6;

/** Tools whose cut-off history result is worth re-running with tighter filters. */
const LISTING_TOOLS = new Set([
    'get_catalog_list',
    'get_schema_list',
    'get_table_list',
    'get_volume_list',
    'search_data_assets',
    'run_sql',
]);

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
        extraContext,
    },
    ref,
) {
    // The message log, streaming flags, route CTA, AND the
    // non-reactive "internals" (AbortController, event-gate clock, tool-name /
    // tool-arg maps, dedup signature) all live in a store keyed by `storageKey`
    // — NOT in this component. That's what lets an in-flight turn survive in-app
    // navigation: when the router unmounts ChatView, the async stream loop keeps
    // writing to the store, and a remounted ChatView simply re-subscribes to the
    // same live session (see stores/chatSessionStore.ts). `internals` replaces
    // what used to be a set of `useRef`s.
    const sessionKey = storageKey || `ephemeral:${mode}`;
    const {
        messages,
        setMessages,
        isStreaming,
        setIsStreaming,
        statusLabel,
        setStatusLabel,
        routeCta,
        setRouteCta,
        resetSession,
        internals,
    } = useChatSession(sessionKey, storageKey);

    const [draft, setDraft] = useState('');
    const [showThinking, setShowThinking] = useState(false);
    const [showModeDropdown, setShowModeDropdown] = useState(false);
    const nextStepsEnabled = useBrandingStore((s) => s.features?.next_step_suggestions !== false);
    const inputRef = useRef<HTMLTextAreaElement | null>(null);
    const messageListRef = useRef<HTMLDivElement | null>(null);
    const modeDropdownRef = useRef<HTMLDivElement | null>(null);
    // Held in a ref so even a continuation turn fired from an older closure reads
    // the host's *current* page state rather than a stale snapshot.
    const extraContextRef = useRef(extraContext);
    extraContextRef.current = extraContext;

    // Scroll only the message list: scrollIntoView would also scroll the page
    // around an embedded chat (e.g. on Discover).
    useEffect(() => {
        const list = messageListRef.current;
        list?.scrollTo({ top: list.scrollHeight, behavior: 'smooth' });
    }, [messages, statusLabel, isStreaming]);

    // NOTE: deliberately NO abort-on-unmount here. A running turn must outlive a
    // route change (the whole point of lifting state into the store); the loop
    // is torn down explicitly via the Stop / New Chat control or superseded when
    // the next turn starts. Persistence is handled by the store's setMessages.

    // Per-second clock for the elapsed-time label inside running tool
    // pills, computed as ``nowMs - msg.startedAt``. Without a periodic
    // update the counter would freeze at "0s" until some other state
    // change (a tool_result, a new pill, the next message) happened to
    // re-render the row. We mount the timer only while at least one
    // pill is actively ``running``.
    const [nowMs, setNowMs] = useState(() => Date.now());
    const hasRunningPill = useMemo(
        () => messages.some((m) => m.kind === 'tool' && m.status === 'running'),
        [messages],
    );
    // Also ticks for the whole of a running turn, which drives the Stop nudge.
    const clockRunning = hasRunningPill || isStreaming;
    useEffect(() => {
        if (!clockRunning) return;
        const id = window.setInterval(() => setNowMs(Date.now()), 1000);
        return () => window.clearInterval(id);
    }, [clockRunning]);

    // Close the mode dropdown when the user clicks anywhere outside
    // it. Mirrors the same pattern Home.tsx uses so the affordance
    // feels identical everywhere the chat appears.
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

    // Walk our display messages and translate them into the wire
    // format the streaming endpoint expects. Settled tool pills become
    // synthetic `tool` messages so the LLM can see what it looked up.
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
    // prefix; we dedupe against their ``tool_call_id``s.
    // Serialize a completed synchronous tool's payload into the ``tool``
    // message content we replay on later turns. Capped so a large listing
    // (e.g. a wide ``get_table_list``) can't dominate the context window;
    // the backend runner prunes oversized tool messages as a further
    // backstop. Falls back to the error text or the human label.
    const HISTORY_TOOL_RESULT_MAX_CHARS = 4000;
    const toolResultForHistory = (
        m: Extract<DisplayMessage, { kind: 'tool' }>,
    ): string => {
        if (m.status === 'error') {
            return `Tool error: ${m.errorMessage ?? 'unknown error'}`;
        }
        let serialized: string;
        if (m.toolResult === undefined || m.toolResult === null) {
            serialized = m.detail ?? m.label ?? '(no output)';
        } else if (typeof m.toolResult === 'string') {
            serialized = m.toolResult;
        } else {
            try {
                serialized = JSON.stringify(m.toolResult);
            } catch {
                serialized = String(m.toolResult);
            }
        }
        if (serialized.length > HISTORY_TOOL_RESULT_MAX_CHARS) {
            // Only a listing has more rows worth fetching; anything else would
            // just be the same answer again.
            const note = LISTING_TOOLS.has(m.toolName)
                ? ' …[truncated; re-run the tool with tighter filters if you need the rest]'
                : ' …[truncated]';
            return serialized.slice(0, HISTORY_TOOL_RESULT_MAX_CHARS) + note;
        }
        return serialized;
    };

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
                    ...(m.suggestions?.length ? { suggestions: m.suggestions } : {}),
                });
            } else if (
                m.kind === 'tool' &&
                (m.status === 'success' || m.status === 'error')
            ) {
                // Round-trip completed tool calls (metadata listings, search,
                // find_owner, workflow execution, …) so the model remembers
                // what it already looked up instead of re-running the same
                // tool (e.g. ``get_table_list``) on every follow-up message.
                if (skipToolCallIds.has(m.toolCallId)) continue;
                const completedAt = new Date(m.completedAt ?? Date.now()).toISOString();
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
                out.push({
                    id: m.id,
                    type: 'tool',
                    content: toolResultForHistory(m),
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
            toolMessages?: AgentChatMessage[];
        } = {},
    ) => {
        const controller = new AbortController();
        internals.abortController?.abort();
        internals.abortController = controller;

        setIsStreaming(true);
        setStatusLabel('Thinking...');
        setShowThinking(false);
        // New turn, new gate — stale "minimum tool-pill duration"
        // bookkeeping from a prior stream shouldn't delay the first
        // event of this one.
        internals.eventGate = 0;
        // Scope the same-message guard to this turn only. A later,
        // unrelated turn that legitimately produces the same short reply
        // (e.g. "Done.") should still render; we only want to swallow a
        // duplicate emitted *within* a single turn.
        internals.lastAgentSig = '';
        internals.turnChart = null;
        // Stale form-route CTAs from a prior turn shouldn't survive a
        // new question — the agent may pick a different form, or no
        // form at all. A continuation keeps it.
        if (!opts.isContinuation) {
            setRouteCta(null);
        }

        // For a normal turn we add a user bubble and stream into a
        // fresh agent slot. For a continuation the user turn is
        // implicit — we just stream the agent reply.
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
                    context: { mode, ...(extraContextRef.current?.() ?? {}) },
                    // Ties the turn to the stored transcript. An ephemeral chat
                    // (no storage key) has no session to tie it to.
                    session_id: storageKey ? getSessionId(storageKey) : undefined,
                },
                { signal: controller.signal },
            )) {
                handleStreamEvent(event);
            }
        } catch (err) {
            // If the stream dies (network error, server/proxy timeout, abort)
            // while a synchronous tool pill is still 'running', it would spin
            // forever — the matching `tool_result` event never arrives. Flip any
            // still-running pills to 'error' so the spinner stops and the failure
            // is visible.
            const failRunningTools = (note: string) =>
                setMessages((prev) =>
                    prev.map((m) =>
                        m.kind === 'tool' && m.status === 'running'
                            ? {
                                ...m,
                                status: 'error',
                                completedAt: Date.now(),
                                errorMessage: m.errorMessage ?? note,
                            }
                            : m,
                    ),
                );
            if (controller.signal.aborted) {
                // User-initiated abort (Stop, New Chat, or a newer turn);
                // surface a quiet agent line and keep the transcript.
                failRunningTools('Stopped');
                setMessages((prev) => [
                    ...prev,
                    {
                        kind: 'agent',
                        id: `${Date.now()}-cancel`,
                        timestamp: new Date().toISOString(),
                        content: '<em>Stopped.</em>',
                    },
                ]);
            } else {
                failRunningTools(errorMessage(err, 'Unknown error'));
                setMessages((prev) => [
                    ...prev,
                    {
                        kind: 'agent',
                        id: `${Date.now()}-err`,
                        timestamp: new Date().toISOString(),
                        content: `<em>I hit an error talking to the agent. ${escapeHtml(
                            errorMessage(err, 'Unknown error'),
                        )}</em>`,
                    },
                ]);
            }
        } finally {
            setIsStreaming(false);
            setStatusLabel(null);
        }
    };

    // The event-gate clock (`internals.eventGate`) is the earliest wall-clock
    // time at which the next agent event may visually apply. It enforces
    // ordering between tool pills and the agent message that follows them: when
    // a synchronous tool resolves in under MIN_RUNNING_MS we delay its visible
    // settle, and we likewise delay the agent's `message` event so it never
    // renders before the pill it depends on. Without this the user reported
    // "everything pops in at once" — the message would show while the pill was
    // still spinning on fast tools.
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
                internals.toolNames[event.id] = event.name;
                internals.toolArgs[event.id] = event.arguments;
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
                onToolResult?.(
                    internals.toolNames[event.id] || '',
                    event.result,
                    event.ok,
                    internals.toolArgs[event.id],
                );
                // A turn shows at most one chart, under its final answer: each
                // chart-worthy result replaces the previous candidate, and `done`
                // attaches the survivor. Intermediate lookups never flash a chart.
                // When the tool asks to re-graph the last answer, bind the
                // conversation's most recent dataset so the chart is self-contained.
                const parsedChart = event.ok
                    ? chartCandidate(event.result, event.name, internals.toolArgs[event.id])
                    : undefined;
                const resolveChart = (prev: DisplayMessage[]): ChatChart | undefined => {
                    if (!parsedChart) return undefined;
                    let dataset = parsedChart.dataset;
                    if (!dataset && parsedChart.bindLast) {
                        const toolMsgs = prev.filter(
                            (m): m is Extract<DisplayMessage, { kind: 'tool' }> => m.kind === 'tool',
                        );
                        dataset = findLastDataset(toolMsgs);
                    }
                    if (!dataset && !parsedChart.spec) return undefined;
                    return { dataset, spec: parsedChart.spec, encoding: parsedChart.encoding };
                };
                const holdChart = (prev: DisplayMessage[]) => {
                    const chart = resolveChart(prev);
                    if (chart) internals.turnChart = chart;
                };
                // Hold the "running" pill on screen for a short
                // minimum so synchronous tools (e.g. execute_workflow,
                // metadata listings) don't flash from "Running …" to
                // "Done" in the same render frame as the agent's
                // follow-up message — the user reported "everything
                // pops in at once" before this. With the floor in
                // place even fast tools render at least one
                // perceivable "running" frame.
                const MIN_RUNNING_MS = 600;
                const settle = () =>
                    setMessages((prev) => {
                        holdChart(prev);
                        return prev.map((m) => {
                            if (m.kind !== 'tool' || m.toolCallId !== event.id) return m;
                            return {
                                ...m,
                                status: event.ok ? 'success' : 'error',
                                completedAt: Date.now(),
                                errorMessage: event.error ?? undefined,
                                label: event.summary || m.label,
                                toolResult: event.result,
                                resultHidden: event.result_hidden || undefined,
                            };
                        });
                    });
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
                    internals.eventGate = Math.max(
                        internals.eventGate,
                        Date.now() + delay,
                    );
                    if (delay === 0) {
                        // Settle synchronously inside this same updater
                        // so the gate's effect on subsequent events is
                        // immediate.
                        holdChart(prev);
                        return prev.map((m) => {
                            if (m.kind !== 'tool' || m.toolCallId !== event.id) return m;
                            return {
                                ...m,
                                status: event.ok ? 'success' : 'error',
                                completedAt: Date.now(),
                                errorMessage: event.error ?? undefined,
                                label: event.summary || m.label,
                                toolResult: event.result,
                                resultHidden: event.result_hidden || undefined,
                            };
                        });
                    }
                    setTimeout(settle, delay);
                    return prev;
                });
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
                // Collapse intra-message self-duplication first. At temperature
                // 0.0 the model occasionally emits the same block twice *inside a
                // single message* (most visible for the long, structured replies
                // the authoring assistant produces). The cross-bubble guards
                // below only compare whole bubbles, so they can't catch a repeat
                // that lives within one MessageEvent — this does.
                const split = splitSuggestions(event.content);
                if (!split.content) break;
                const suggestions = sanitizeSuggestions(event.suggestions ?? split.suggestions);
                const content = collapseSelfDuplication(split.content);
                const incomingSig = normalizeForDedup(content);
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
                        if (incomingSig && incomingSig === internals.lastAgentSig) {
                            return prev;
                        }
                        // Find the most recent *conversational* bubble (ignore
                        // tool pills / reasoning). The backend yields at most one
                        // agent MessageEvent per turn, so if the last such bubble
                        // is already an agent message — with no user turn since —
                        // a second agent bubble is a duplicate. We accept it only
                        // if it's genuinely different content; near-identical text
                        // (same long prefix, e.g. the model re-emitting a slightly
                        // reworded answer at temperature 0.0) is swallowed.
                        const lastConversational = [...prev]
                            .reverse()
                            .find(
                                (m): m is Extract<DisplayMessage, { kind: 'user' | 'agent' }> =>
                                    m.kind === 'agent' || m.kind === 'user',
                            );
                        if (
                            lastConversational?.kind === 'agent' &&
                            isLikelyDuplicateAgentMessage(lastConversational.content, content)
                        ) {
                            return prev;
                        }
                        internals.lastAgentSig = incomingSig;
                        return [
                            ...prev,
                            {
                                kind: 'agent',
                                id: `${Date.now()}-m`,
                                content,
                                timestamp: new Date().toISOString(),
                                ...(suggestions.length ? { suggestions } : {}),
                            },
                        ];
                    });
                // Honor the tool-pill gate so messages never render
                // before their preceding tool resolves visually.
                const wait = Math.max(0, internals.eventGate - Date.now());
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
                });
                break;
            }
            case 'done': {
                setStatusLabel(null);
                const chart = internals.turnChart;
                internals.turnChart = null;
                if (chart) {
                    // After the gate, so the final message (also gated) is in place.
                    const wait = Math.max(0, internals.eventGate - Date.now()) + 50;
                    setTimeout(() => setMessages((prev) => attachTurnChart(prev, chart)), wait);
                }
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

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        const text = draft.trim();
        if (!text || isStreaming) return;
        setDraft('');
        await submitTurn(text);
    };

    const lastMessage = messages[messages.length - 1];
    const nextSteps =
        nextStepsEnabled && !isStreaming && lastMessage?.kind === 'agent'
            ? (lastMessage.suggestions ?? [])
            : [];

    const handleNextStep = (text: string) => {
        if (isStreaming) return;
        // The chip unmounts as soon as the turn starts; keep keyboard focus in the chat.
        inputRef.current?.focus();
        void submitTurn(text);
    };

    const handleSamplePrompt = (text: string) => {
        if (isStreaming) return;
        setDraft('');
        void submitTurn(text);
    };

    // Expose a tiny imperative API so the parent can submit a query
    // programmatically — used by Self Service to forward `autoQuery`
    // navigation state and to wire its rich discovery cards into the
    // streaming pipeline. Refs intentionally stay narrow; if more
    // surfaces grow needs, prefer adding focused props before
    // expanding this handle. Rebuilt every render (no deps) so it always
    // calls the current `submitTurn`, which closes over this render's state.
    useImperativeHandle(ref, () => ({
        submitQuery: (text: string) => {
            const trimmed = text.trim();
            if (!trimmed || isStreaming) return;
            setDraft('');
            void submitTurn(trimmed);
        },
    }));

    const handleStop = () => {
        internals.abortController?.abort();
        setStatusLabel(null);
    };

    const isBusy = isStreaming;
    // Nudge toward Stop when a turn runs long or fans out into many steps.
    const turnStats = useMemo(() => {
        let lastUser = -1;
        for (let i = messages.length - 1; i >= 0; i -= 1) {
            if (messages[i].kind === 'user') {
                lastUser = i;
                break;
            }
        }
        const startedAt =
            lastUser >= 0 ? Date.parse((messages[lastUser] as { timestamp: string }).timestamp) : NaN;
        const toolCount = messages.slice(lastUser + 1).filter((m) => m.kind === 'tool').length;
        return { startedAt, toolCount };
    }, [messages]);
    const showStopNudge =
        isBusy &&
        (turnStats.toolCount >= STOP_NUDGE_TOOL_CALLS ||
            (Number.isFinite(turnStats.startedAt) && nowMs - turnStats.startedAt >= STOP_NUDGE_MS));

    const handleClear = () => {
        // Aborts any in-flight turn, clears the internals, resets the reactive
        // state, and removes the persisted log — all in the store.
        resetSession();
        setDraft('');
    };

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
            {messages.length === 0 && emptyStateExtras !== undefined ? (
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
                                    autoResize
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
                                    <PromptChip
                                        key={q}
                                        text={q}
                                        onSelect={handleSamplePrompt}
                                        disabled={isStreaming}
                                    />
                                ))}
                            </div>
                        )}
                    </div>
                    {emptyStateExtras && <div className="mt-10">{emptyStateExtras}</div>}
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
                                <PromptChip
                                    key={q}
                                    text={q}
                                    onSelect={handleSamplePrompt}
                                    disabled={isStreaming}
                                />
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
                                autoResize
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
                    <div ref={messageListRef} className="flex-1 overflow-y-auto space-y-4 pr-2 custom-scrollbar">
                        {renderMessages(messages, nowMs)}

                        <NextStepSuggestions suggestions={nextSteps} onSelect={handleNextStep} />

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

                        {showStopNudge && (
                            <div className="flex items-center gap-2 text-xs text-gray-500">
                                Still working on this.
                                <button
                                    type="button"
                                    onClick={handleStop}
                                    className="underline underline-offset-2 hover:text-gray-800"
                                >
                                    Stop
                                </button>
                                if it&rsquo;s not what you need.
                            </div>
                        )}

                        {routeCtaNode}
                    </div>

                    {/* Input — "New Chat" sits inline with Send to mirror
                        the Self Service / Home view. Single, consistent
                        affordance instead of a separate top-right link. */}
                    <form onSubmit={handleSubmit} className="border-t border-gray-200/50 pt-4 mt-4">
                        <div className="flex gap-2 items-center">
                            <Textarea
                                ref={inputRef}
                                value={draft}
                                onChange={(e) => setDraft(e.target.value)}
                                placeholder={placeholder}
                                rows={1}
                                autoResize
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' && !e.shiftKey) {
                                        e.preventDefault();
                                        const form = e.currentTarget.closest('form');
                                        if (form && !isStreaming) {
                                            form.requestSubmit();
                                        }
                                    }
                                }}
                                className="flex-1 rounded-xl border-2 border-gray-200 focus:border-primary/50 focus:ring-4 focus:ring-primary/10 transition-all duration-200 min-h-[48px] max-h-[200px] py-3"
                            />
                            {isBusy ? (
                                <Button
                                    type="button"
                                    onClick={handleStop}
                                    title="Stop the assistant (keeps the conversation)"
                                    aria-label="Stop"
                                    className="rounded-xl shadow-md hover:shadow-lg transition-all duration-200 h-10"
                                >
                                    <Square className="w-4 h-4 text-white fill-white" />
                                </Button>
                            ) : (
                                <Button
                                    type="submit"
                                    disabled={!draft.trim()}
                                    className="rounded-xl shadow-md hover:shadow-lg transition-all duration-200 h-10"
                                >
                                    <Send className="w-4 h-4 text-white" />
                                </Button>
                            )}
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
                        {messages.length > 0 && (
                            <div className="flex justify-end mt-1">
                                <button
                                    type="button"
                                    onClick={() => downloadTranscript(messages)}
                                    title="Download this conversation, including tool calls, as Markdown"
                                    className="inline-flex items-center gap-1 text-[10px] text-gray-400 hover:text-gray-600 transition-colors"
                                >
                                    <Download className="w-3 h-3" />
                                    Download transcript
                                </button>
                            </div>
                        )}
                    </form>
                </>
            )}
        </div>
    );
});

/**
 * Put the turn's chart on its final agent message (the last agent bubble after
 * the last user turn), or on the turn's last tool pill when the turn ended
 * without a message.
 */
function attachTurnChart(prev: DisplayMessage[], chart: ChatChart): DisplayMessage[] {
    let lastUser = -1;
    for (let i = prev.length - 1; i >= 0; i -= 1) {
        if (prev[i].kind === 'user') {
            lastUser = i;
            break;
        }
    }
    for (const kind of ['agent', 'tool'] as const) {
        for (let i = prev.length - 1; i > lastUser; i -= 1) {
            if (prev[i].kind === kind) {
                const next = prev.slice();
                next[i] = { ...prev[i], chart } as DisplayMessage;
                return next;
            }
        }
    }
    return prev;
}

function ChartBlock({ chart }: { chart: ChatChart }) {
    if (chart.dataset) {
        return (
            <ChartPanel
                dataset={chart.dataset}
                initialEncoding={chart.encoding}
                initialSpec={chart.spec}
                defaultControlsOpen
            />
        );
    }
    if (!chart.spec) return null;
    return (
        <div className="bg-white border border-gray-200 rounded-md p-2">
            <VegaLiteChart spec={chart.spec} />
        </div>
    );
}

function MessageRow({
    msg,
    nowMs,
}: {
    msg: DisplayMessage;
    /** Current time from the parent's per-second clock. */
    nowMs: number;
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
        // Agent messages are stored as markdown. We render to HTML at view time via
        // `renderMarkdownSafe` so the `prose` class can style
        // headings, tables, code blocks, etc. consistently. Legacy
        // persisted HTML content passes through marked unchanged.
        // The `agent-prose` class layers on chat-appropriate spacing
        // and heading sizes (the typography plugin's defaults are
        // calibrated for a blog post and look obnoxiously large
        // inside a chat bubble).
        const html = renderMarkdownSafe(msg.content);
        return (
            <div className="flex flex-col items-start animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div className="max-w-[80%] rounded-2xl px-4 py-3 shadow-sm bg-gray-50 text-gray-900 border border-gray-200/50">
                    <div
                        className="text-sm leading-relaxed prose prose-sm agent-prose max-w-none [&_a]:text-blue-600 [&_a]:underline [&_a]:underline-offset-2 [&_a:hover]:text-blue-700"
                        dangerouslySetInnerHTML={{ __html: html }}
                    />
                </div>
                {msg.chart && (msg.chart.dataset || msg.chart.spec) && (
                    <div className="w-full mt-2">
                        <ChartBlock chart={msg.chart} />
                    </div>
                )}
            </div>
        );
    }
    if (msg.kind === 'tool') {
        const elapsed =
            msg.status === 'running' && msg.startedAt
                ? formatElapsed(nowMs - msg.startedAt)
                : (msg.status === 'success' || msg.status === 'error') &&
                    msg.startedAt && msg.completedAt
                    ? // Settled pill — keep the total runtime visible so the
                      // user can see "this tool took 8s" at a glance.
                      formatElapsed(msg.completedAt - msg.startedAt)
                    : undefined;
        // Synchronous tools: surface the raw result in a collapsible
        // expander so SAs can verify exactly what data the agent saw.
        // Skip when there's nothing to show (errors that didn't return a
        // body, in-flight pills, etc.).
        const chart = msg.chart;
        const hasChart = !!chart && (!!chart.dataset || !!chart.spec);
        const sqlResult = msg.status === 'success' ? asSqlResult(msg.toolResult) : null;
        const showRawOutput =
            !hasChart &&
            !sqlResult &&
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
                {hasChart && chart && (
                    <div className="w-full mt-2">
                        <ChartBlock chart={chart} />
                    </div>
                )}
                {sqlResult && <SqlResultPanel result={sqlResult} showRows={!hasChart} />}
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
    nowMs: number,
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
                    nowMs={nowMs}
                />,
            );
            continue;
        }
        out.push(
            <MessageRow
                key={m.id}
                msg={m}
                nowMs={nowMs}
            />,
        );
        i++;
    }
    return out;
}

/**
 * Renders a run of consecutive tool calls. By default only the most
 * recent pill is visible; a small toggle reveals the earlier steps.
 * If a step is currently `running` we keep it visible
 * regardless so the user can see the live activity.
 */
function ToolCallGroup({
    items,
    nowMs,
}: {
    items: Extract<DisplayMessage, { kind: 'tool' }>[];
    nowMs: number;
}) {
    const [expanded, setExpanded] = useState(false);

    if (items.length === 1) {
        return (
            <MessageRow
                msg={items[0]}
                nowMs={nowMs}
            />
        );
    }

    const last = items[items.length - 1];
    const earlier = items.slice(0, -1);
    // If something in the earlier batch is still mid-flight, keep it
    // visible — hiding an in-progress spinner is confusing.
    const hasRunningEarlier = earlier.some(
        (m) => m.status === 'running',
    );
    const showEarlier = expanded || hasRunningEarlier;

    return (
        <div className="flex flex-col gap-1.5 min-w-0 animate-in fade-in slide-in-from-bottom-2 duration-300">
            {showEarlier &&
                earlier.map((m) => (
                    <MessageRow
                        key={m.id}
                        msg={m}
                        nowMs={nowMs}
                    />
                ))}
            <MessageRow
                msg={last}
                nowMs={nowMs}
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

// Decide whether `incoming` is a duplicate of the immediately-preceding agent
// bubble `prevContent`. Used only when no user turn separates them (the backend
// emits one agent message per turn). Catches both exact repeats and the
// "reworded near-duplicate" case (identical long prefix) we've seen the model
// produce across iterations at temperature 0.0.
function isLikelyDuplicateAgentMessage(prevContent: string, incoming: string): boolean {
    const a = normalizeForDedup(prevContent);
    const b = normalizeForDedup(incoming);
    if (!a || !b) return false;
    if (a === b) return true;
    // Identical opening of a substantial message ⇒ same answer, lightly reworded.
    const PREFIX = 80;
    if (a.length >= PREFIX && b.length >= PREFIX && a.slice(0, PREFIX) === b.slice(0, PREFIX)) {
        return true;
    }
    // One fully contains the other (e.g. a trailing sentence was added/removed).
    const [shorter, longer] = a.length <= b.length ? [a, b] : [b, a];
    if (shorter.length >= 40 && longer.includes(shorter)) return true;
    return false;
}

// Collapse an agent message that repeats its own content. We've observed the
// model (running at temperature 0.0) re-emit the same block twice within a
// single reply — usually the long, multi-paragraph answers the authoring
// assistant produces. Returns the de-duplicated text, or the original string
// untouched when no clean self-duplication is found. Conservative by design:
// it only collapses when a substantial span repeats *exactly* (whitespace
// aside), so it can't eat legitimately-repeated short phrases.
function collapseSelfDuplication(raw: string): string {
    const text = raw.trim();
    if (text.length < 120) return raw;

    // Paragraph-aware path: split on blank lines (markdown block boundaries)
    // and detect either whole-message doubling (first half of blocks repeats
    // as the second half) or a trailing run of blocks that repeats the run
    // immediately before it. This preserves the original formatting of the
    // copy we keep.
    const blocks = text.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
    if (blocks.length >= 2) {
        const sig = blocks.map(normalizeForDedup);
        const total = sig.join(' ').length;
        if (blocks.length % 2 === 0 && total >= 60) {
            const half = blocks.length / 2;
            let mirror = true;
            for (let i = 0; i < half; i++) {
                if (sig[i] !== sig[i + half]) {
                    mirror = false;
                    break;
                }
            }
            if (mirror) return blocks.slice(0, half).join('\n\n');
        }
        // Trailing repeated run: the last k blocks exactly repeat the k blocks
        // before them. Take the largest such k.
        for (let k = Math.floor(blocks.length / 2); k >= 1; k--) {
            const tail = sig.slice(blocks.length - k);
            const prev = sig.slice(blocks.length - 2 * k, blocks.length - k);
            if (
                tail.length === k &&
                prev.length === k &&
                tail.join(' ').length >= 60 &&
                tail.every((s, i) => s === prev[i])
            ) {
                return blocks.slice(0, blocks.length - k).join('\n\n');
            }
        }
    }

    // Single-block fallback: the whole normalized string is "A A" (with the
    // joining whitespace normalized to at most one space).
    const n = normalizeForDedup(text);
    const L = n.length;
    for (const s of [0, 1]) {
        if ((L - s) % 2 !== 0) continue;
        const half = (L - s) / 2;
        if (half < 60) continue;
        const prefix = n.slice(0, half);
        const sep = n.slice(half, half + s);
        const rest = n.slice(half + s);
        if (prefix === rest && (s === 0 || sep === ' ')) {
            // Map the normalized boundary back to the raw string: keep the
            // shortest raw prefix whose normalized form equals the first copy.
            for (let i = 1; i < text.length; i++) {
                if (normalizeForDedup(text.slice(0, i)) === prefix) {
                    return text.slice(0, i).trim();
                }
            }
        }
    }

    // Near-duplicate restart: the model re-answered the whole thing, repeating a
    // substantial opening verbatim before diverging with light rewording (e.g.
    // "...should the approval be..." vs "...should approval be..."). The
    // exact-match paths above can't catch this because the two copies aren't
    // byte-identical. We look for a second occurrence of a long opening head and
    // require the two copies to be similar in length (so we never swallow
    // genuinely different trailing content), then keep the first copy.
    if (n.length >= 160) {
        const HEAD = 40;
        const head = n.slice(0, HEAD);
        const second = n.indexOf(head, HEAD);
        if (second > 0) {
            const copy1 = n.slice(0, second).trim();
            const copy2 = n.slice(second).trim();
            const ratio =
                Math.min(copy1.length, copy2.length) /
                Math.max(copy1.length, copy2.length);
            if (copy1.length >= 60 && copy2.length >= 60 && ratio >= 0.6) {
                for (let i = 1; i <= text.length; i++) {
                    if (normalizeForDedup(text.slice(0, i)) === copy1) {
                        return text.slice(0, i).trim();
                    }
                }
            }
        }
    }

    return raw;
}

/** Default label for the route CTA when the parent doesn't override. */
function defaultRouteLabel(path: string): string {
    if (!path) return 'Continue to form';
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
 * Compact mode picker rendered beneath the input bar, shared by every chat
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
