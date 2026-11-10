/**
 * Chat session store — lifts a `ChatView`'s message log + streaming lifecycle
 * OUT of the React component tree so an in-flight agent turn survives in-app
 * navigation (route changes unmount `ChatView`, but this store does not).
 *
 * Two layers per session, keyed by the caller's `storageKey`:
 *   * reactive state (messages / isStreaming / statusLabel / routeCta)
 *     in the Zustand store — what the view renders.
 *   * non-reactive `internals` (AbortController, event-gate clock, tool-name /
 *     tool-arg maps, last-applied-signature) in a plain module Map — the
 *     mutable "refs" the SSE loop reads/writes. Because both live outside the
 *     component, the async stream loop keeps updating them after unmount and
 *     the view simply re-subscribes when it remounts.
 *
 * NOTE: this makes a running turn survive *route navigation*, not a full page
 * reload — on reload the store is empty and we re-hydrate messages from the
 * localStorage cache, dropping any pill that was still in-flight (its stream is
 * gone).
 *
 * Durable storage is the server (see `lib/chatPersistence.ts`); localStorage is
 * kept as a synchronous cache so first paint doesn't wait on a fetch.
 */
import { useCallback, useEffect, useMemo } from 'react';
import { create } from 'zustand';

import type { ChatChart, ChatRouteInfo, DisplayMessage } from '../components/chat/chatTypes';
import {
    discard as discardTranscript,
    hydrateFromServer,
    readCache,
    scheduleSave,
    writeCache,
} from '../lib/chatPersistence';
import { api } from '../services/api';

// ---------------------------------------------------------------------------
// Non-reactive per-session internals (the former `useRef`s). Kept in a module
// Map so they persist across component unmount/remount just like the store.
// ---------------------------------------------------------------------------
export interface ChatSessionInternals {
    abortController: AbortController | null;
    /** Earliest wall-clock ms at which the next agent event may visually apply. */
    eventGate: number;
    /** tool_call id -> tool name. */
    toolNames: Record<string, string>;
    /** tool_call id -> raw arguments. */
    toolArgs: Record<string, Record<string, unknown> | undefined>;
    /** Whitespace-normalized signature of the last applied agent message. */
    lastAgentSig: string;
    /** The latest chart a tool produced this turn; attached to the final answer on `done`. */
    turnChart: ChatChart | null;
    /** localStorage key for this session ('' disables persistence). */
    persistKey: string;
}

const internalsMap = new Map<string, ChatSessionInternals>();

export function getChatInternals(key: string): ChatSessionInternals {
    let internals = internalsMap.get(key);
    if (!internals) {
        internals = {
            abortController: null,
            eventGate: 0,
            toolNames: {},
            toolArgs: {},
            lastAgentSig: '',
            turnChart: null,
            persistKey: '',
        };
        internalsMap.set(key, internals);
    }
    return internals;
}

// ---------------------------------------------------------------------------
// Reactive per-session state
// ---------------------------------------------------------------------------
export interface ChatSessionState {
    messages: DisplayMessage[];
    isStreaming: boolean;
    statusLabel: string | null;
    routeCta: ChatRouteInfo | null;
}

const EMPTY_MESSAGES: DisplayMessage[] = [];

function freshState(): ChatSessionState {
    return {
        messages: EMPTY_MESSAGES,
        isStreaming: false,
        statusLabel: null,
        routeCta: null,
    };
}

/** Strip pills whose stream died with the previous page load. */
function dropInFlight(messages: DisplayMessage[]): DisplayMessage[] {
    // After a full reload an unfinished tool entry would spin forever. (On in-app
    // navigation the session stays live in this store and is NOT re-hydrated, so
    // genuinely-running pills are preserved.)
    return messages.filter(
        (m) => !(m.kind === 'tool' && m.status !== 'success' && m.status !== 'error'),
    );
}

function hydrateMessages(persistKey: string): DisplayMessage[] {
    const cached = readCache(persistKey);
    return cached ? dropInFlight(cached) : EMPTY_MESSAGES;
}

type Updater<T> = T | ((prev: T) => T);
function applyUpdater<T>(updater: Updater<T>, prev: T): T {
    return typeof updater === 'function' ? (updater as (p: T) => T)(prev) : updater;
}

interface ChatSessionStore {
    sessions: Record<string, ChatSessionState>;
    ensureSession: (key: string, persistKey: string) => void;
    setMessages: (key: string, updater: Updater<DisplayMessage[]>) => void;
    setIsStreaming: (key: string, value: boolean) => void;
    setStatusLabel: (key: string, value: string | null) => void;
    setRouteCta: (key: string, value: ChatRouteInfo | null) => void;
    resetSession: (key: string) => void;
}

export const useChatSessionStore = create<ChatSessionStore>((set, get) => {
    // Ensure a slot exists, hydrating messages from localStorage the first time.
    // Every mutator funnels through this so an early setter (e.g. setIsStreaming
    // fires before the first setMessages in a turn) can never clobber persisted
    // history with an empty log.
    const ensureSlot = (
        sessions: Record<string, ChatSessionState>,
        key: string,
    ): ChatSessionState => {
        const existing = sessions[key];
        if (existing) return existing;
        return { ...freshState(), messages: hydrateMessages(getChatInternals(key).persistKey) };
    };

    return {
        sessions: {},
        ensureSession: (key, persistKey) => {
            getChatInternals(key).persistKey = persistKey;
            if (get().sessions[key]) return;
            set((s) => ({
                sessions: {
                    ...s.sessions,
                    [key]: { ...freshState(), messages: hydrateMessages(persistKey) },
                },
            }));
        },
        setMessages: (key, updater) => {
            set((s) => {
                const cur = ensureSlot(s.sessions, key);
                const messages = applyUpdater(updater, cur.messages);
                const { persistKey } = getChatInternals(key);
                if (persistKey) {
                    // Cache synchronously (this runs on every streamed token, so
                    // it has to be cheap) and let the server write debounce.
                    writeCache(persistKey, messages);
                    scheduleSave(persistKey, messages);
                }
                return { sessions: { ...s.sessions, [key]: { ...cur, messages } } };
            });
        },
        setIsStreaming: (key, value) =>
            set((s) => {
                const cur = ensureSlot(s.sessions, key);
                return { sessions: { ...s.sessions, [key]: { ...cur, isStreaming: value } } };
            }),
        setStatusLabel: (key, value) =>
            set((s) => {
                const cur = ensureSlot(s.sessions, key);
                return { sessions: { ...s.sessions, [key]: { ...cur, statusLabel: value } } };
            }),
        setRouteCta: (key, value) =>
            set((s) => {
                const cur = ensureSlot(s.sessions, key);
                return { sessions: { ...s.sessions, [key]: { ...cur, routeCta: value } } };
            }),
        resetSession: (key) => {
            const internals = getChatInternals(key);
            internals.abortController?.abort();
            internals.abortController = null;
            internals.eventGate = 0;
            internals.toolNames = {};
            internals.toolArgs = {};
            internals.lastAgentSig = '';
            internals.turnChart = null;
            if (internals.persistKey) {
                // Clears the cache, deletes the stored transcript, and mints a
                // new session id so the next conversation starts clean.
                discardTranscript(internals.persistKey);
            }
            set((s) => ({ sessions: { ...s.sessions, [key]: freshState() } }));
        },
    };
});

/** Storage key of the main chat on the home page. */
export const FULL_CHAT_STORAGE_KEY = 'chatview_messages_unified';

/**
 * Append one persisted chat's messages to another and clear the source, e.g. to
 * carry an in-page assistant conversation into the full chat. Both keys are
 * storage keys (the session key of a persisted `ChatView`). Returns whether
 * anything was moved.
 */
export function moveChatMessages(fromKey: string, toKey: string): boolean {
    const store = useChatSessionStore.getState();
    store.ensureSession(fromKey, fromKey);
    const moving = useChatSessionStore.getState().sessions[fromKey]?.messages ?? [];
    if (moving.length === 0) return false;
    store.ensureSession(toKey, toKey);
    store.setMessages(toKey, (prev) => [...prev, ...moving]);
    store.resetSession(fromKey);
    return true;
}

/**
 * Hook that binds a `ChatView` to its session slice. Returns the reactive
 * state plus setters whose signatures mirror React's `useState` dispatch (value
 * or updater) so the component body reads exactly as it did with local state.
 */
export function useChatSession(sessionKey: string, persistKey: string) {
    // Record the persist key synchronously (plain Map mutation — not a store
    // `set`, so no render-phase update) so an early setter can persist before
    // the ensure effect below runs.
    getChatInternals(sessionKey).persistKey = persistKey;

    const stored = useChatSessionStore((s) => s.sessions[sessionKey]);

    // First-render fallback: hydrate persisted messages so the log shows
    // immediately, before the effect populates the store slot. On in-app
    // navigation the slot already exists (the session stayed live), so `stored`
    // wins and no re-hydration happens.
    const fallback = useMemo<ChatSessionState>(
        () => ({ ...freshState(), messages: hydrateMessages(persistKey) }),
        [persistKey],
    );
    const session = stored ?? fallback;

    // Create the store slot after mount (idempotent). Kept out of render to
    // avoid a Zustand `set` during another component's render.
    useEffect(() => {
        const store = useChatSessionStore.getState();
        store.ensureSession(sessionKey, persistKey);

        // Pull the transcript from the server for the case localStorage can't
        // cover: a different device, or a cleared cache. Only applies when there
        // is nothing local, so it can never clobber a live conversation.
        void hydrateFromServer(
            persistKey,
            store.sessions[sessionKey]?.messages ?? [],
            (messages) => useChatSessionStore.getState().setMessages(sessionKey, messages),
        );

        // A tab open since this morning has long-stale user context. This is the
        // last moment before a message can be sent, so re-warm it; the backend's
        // minimum-refresh floor makes this free when boot already did the work.
        void api.warmUserContext();
    }, [sessionKey, persistKey]);

    const setMessages = useCallback(
        (updater: Updater<DisplayMessage[]>) =>
            useChatSessionStore.getState().setMessages(sessionKey, updater),
        [sessionKey],
    );
    const setIsStreaming = useCallback(
        (value: boolean) => useChatSessionStore.getState().setIsStreaming(sessionKey, value),
        [sessionKey],
    );
    const setStatusLabel = useCallback(
        (value: string | null) => useChatSessionStore.getState().setStatusLabel(sessionKey, value),
        [sessionKey],
    );
    const setRouteCta = useCallback(
        (value: ChatRouteInfo | null) => useChatSessionStore.getState().setRouteCta(sessionKey, value),
        [sessionKey],
    );
    const resetSession = useCallback(
        () => useChatSessionStore.getState().resetSession(sessionKey),
        [sessionKey],
    );

    return {
        messages: session.messages,
        isStreaming: session.isStreaming,
        statusLabel: session.statusLabel,
        routeCta: session.routeCta,
        setMessages,
        setIsStreaming,
        setStatusLabel,
        setRouteCta,
        resetSession,
        internals: getChatInternals(sessionKey),
    };
}
