/**
 * Where a chat transcript lives.
 *
 * Transcripts used to exist only in `localStorage`, so they vanished on a cache
 * clear, never followed the user to a second device, and the backend could not
 * see them (which meant "what has this user been working on" was unanswerable).
 * The server is now the durable home; `localStorage` stays on as a synchronous
 * cache so the log still paints instantly on reload instead of waiting for a
 * fetch.
 *
 * Writes are debounced. `setMessages` fires on every streamed token, and each of
 * those must not become a PUT.
 */
import { api } from '../services/api';
import type { DisplayMessage } from '../components/chat/chatTypes';

/** Trailing debounce for server writes. Streaming settles well inside this. */
const WRITE_DEBOUNCE_MS = 2000;

/** Maps a `ChatView` persist key to a backend surface. */
export function surfaceForKey(persistKey: string): string {
    if (persistKey.includes('authoring')) return 'authoring';
    if (persistKey.includes('discover')) return 'discover';
    return 'unified';
}

/**
 * Stable session id per surface, kept beside the cached transcript.
 *
 * The UI shows one conversation per surface, so the id only needs to be stable
 * across reloads — not user-selectable. Resetting the chat mints a new one so the
 * cleared conversation isn't resurrected.
 */
function sessionIdKey(persistKey: string): string {
    return `${persistKey}__session_id`;
}

function randomId(): string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }
    return `sess-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function getSessionId(persistKey: string): string {
    if (!persistKey || typeof window === 'undefined') return '';
    const key = sessionIdKey(persistKey);
    try {
        const existing = window.localStorage.getItem(key);
        if (existing) return existing;
        const created = randomId();
        window.localStorage.setItem(key, created);
        return created;
    } catch {
        // Storage disabled: fall back to a per-load id. Persistence degrades to
        // in-memory only, which is the old pre-server behavior.
        return randomId();
    }
}

function rotateSessionId(persistKey: string): void {
    if (!persistKey || typeof window === 'undefined') return;
    try {
        window.localStorage.setItem(sessionIdKey(persistKey), randomId());
    } catch {
        /* storage disabled — non-fatal */
    }
}

// --- Local cache -----------------------------------------------------------

/** Synchronous read of the cached transcript, for first paint. */
export function readCache(persistKey: string): DisplayMessage[] | null {
    if (!persistKey || typeof window === 'undefined') return null;
    try {
        const raw = window.localStorage.getItem(persistKey);
        if (!raw) return null;
        const parsed = JSON.parse(raw) as DisplayMessage[];
        return Array.isArray(parsed) ? parsed : null;
    } catch {
        return null;
    }
}

export function writeCache(persistKey: string, messages: DisplayMessage[]): void {
    if (!persistKey || typeof window === 'undefined') return;
    try {
        window.localStorage.setItem(persistKey, JSON.stringify(messages));
    } catch {
        /* storage quota / disabled — non-fatal */
    }
}

function clearCache(persistKey: string): void {
    if (!persistKey || typeof window === 'undefined') return;
    try {
        window.localStorage.removeItem(persistKey);
    } catch {
        /* swallow */
    }
}

// --- Cache ownership -------------------------------------------------------

/** Which user the transcripts sitting in `localStorage` belong to. */
const OWNER_KEY = 'chat_cache_owner';
/** Every `ChatView` persist key starts with this; the session ids hang off them. */
const KEY_PREFIX = 'chatview_messages_';

/**
 * The user this page load has *confirmed* owns the local cache.
 *
 * Null until `/users/me` comes back. Server writes wait for it, because
 * `localStorage` is shared by everyone who uses this browser profile while the
 * transcripts on the server are per-user: writing before we know who is here
 * risks filing the previous user's conversation under the current one's account.
 */
let confirmedOwner: string | null = null;

// --- Server writes ---------------------------------------------------------

interface PendingWrite {
    timer: ReturnType<typeof setTimeout>;
    messages: DisplayMessage[];
    attempts: number;
}

const pendingWrites = new Map<string, PendingWrite>();

/** How many debounce intervals a save waits for the owner before giving up. */
const MAX_OWNER_WAITS = 3;

function put(persistKey: string, messages: DisplayMessage[]): void {
    const sessionId = getSessionId(persistKey);
    if (!sessionId) return;
    void api.putChatSession(sessionId, messages, surfaceForKey(persistKey)).catch(() => {
        /* local cache is still authoritative for this browser */
    });
}

/**
 * Queue a debounced save of the whole transcript.
 *
 * Failures are swallowed: the local cache already holds the messages, so a
 * dropped save costs cross-device sync, not the user's conversation.
 */
export function scheduleSave(persistKey: string, messages: DisplayMessage[]): void {
    if (!persistKey) return;
    const existing = pendingWrites.get(persistKey);
    if (existing) clearTimeout(existing.timer);
    const attempts = existing ? existing.attempts : 0;

    const timer = setTimeout(() => {
        pendingWrites.delete(persistKey);
        if (!confirmedOwner) {
            // Identity hasn't landed yet. Wait a beat rather than filing this
            // transcript under a user we haven't confirmed — but don't wait
            // forever, or a failing `/users/me` would spin.
            if (attempts + 1 < MAX_OWNER_WAITS) {
                scheduleSave(persistKey, messages);
                const requeued = pendingWrites.get(persistKey);
                if (requeued) requeued.attempts = attempts + 1;
            }
            return;
        }
        put(persistKey, messages);
    }, WRITE_DEBOUNCE_MS);

    pendingWrites.set(persistKey, { timer, messages, attempts });
}

/**
 * Send queued saves now.
 *
 * The debounce means closing the tab within a couple of seconds of the last
 * token would otherwise leave that turn on this device only.
 */
export function flushPendingSaves(): void {
    if (!confirmedOwner) return;
    pendingWrites.forEach(({ timer, messages }, persistKey) => {
        clearTimeout(timer);
        put(persistKey, messages);
    });
    pendingWrites.clear();
}

if (typeof window !== 'undefined') {
    // `pagehide` fires for tab close, navigation, *and* mobile backgrounding,
    // which `beforeunload` does not.
    window.addEventListener('pagehide', flushPendingSaves);
}

/** Drop a queued save — used when the transcript is being deleted anyway. */
function cancelSave(persistKey: string): void {
    const existing = pendingWrites.get(persistKey);
    if (existing) {
        clearTimeout(existing.timer);
        pendingWrites.delete(persistKey);
    }
}

// --- Hydration -------------------------------------------------------------

// Surfaces already checked against the server this page load, so remounting a
// ChatView doesn't re-fetch.
const hydrated = new Set<string>();

/** In-flight session reads, so concurrent callers share one request. */
const inFlightReads = new Map<string, Promise<DisplayMessage[] | null>>();

/**
 * Read a stored transcript, collapsing concurrent reads of the same session.
 *
 * Hydration and the recent-topics lookup both run on mount and both want the
 * same session, which was costing two identical round trips on every boot. The
 * entry is dropped as soon as it settles, so this dedupes without ever serving a
 * stale transcript.
 */
function fetchSession(sessionId: string): Promise<DisplayMessage[] | null> {
    const existing = inFlightReads.get(sessionId);
    if (existing) return existing;

    const read = api
        .getChatSession(sessionId)
        .then((session) => (session?.messages as DisplayMessage[] | undefined) ?? null)
        .catch(() => null)
        .finally(() => inFlightReads.delete(sessionId));

    inFlightReads.set(sessionId, read);
    return read;
}

/**
 * Adopt the server's transcript, but only when there is nothing local to lose.
 *
 * This is the new-device / cleared-cache path. We deliberately never overwrite a
 * non-empty local log: a turn may be streaming into it right now, and the server
 * copy is by definition a debounce-interval behind.
 */
export async function hydrateFromServer(
    persistKey: string,
    localMessages: DisplayMessage[],
    apply: (messages: DisplayMessage[]) => void,
): Promise<void> {
    if (!persistKey || hydrated.has(persistKey)) return;
    hydrated.add(persistKey);
    if (localMessages.length > 0) return;

    const sessionId = getSessionId(persistKey);
    if (!sessionId) return;
    // Never throws: offline or not-yet-saved just leaves the local cache standing.
    const messages = (await fetchSession(sessionId)) ?? [];
    if (messages.length > 0) {
        writeCache(persistKey, messages);
        apply(messages);
    }
}

/**
 * The user's most recent questions, for personalizing starting suggestions.
 *
 * Prefers the stored transcript so this works on a device the user has never
 * chatted from, and falls back to the local cache when the server has nothing
 * yet (offline, or before the first debounced save).
 */
export async function getRecentUserTopics(persistKey: string, limit = 5): Promise<string[]> {
    let messages: DisplayMessage[] | null = null;
    const sessionId = getSessionId(persistKey);
    if (sessionId) messages = await fetchSession(sessionId);
    if (!messages || messages.length === 0) messages = readCache(persistKey);
    if (!messages) return [];

    const texts = messages
        .filter((m): m is DisplayMessage & { content: string } =>
            m?.kind === 'user' && typeof (m as { content?: unknown }).content === 'string')
        .map((m) => m.content.trim())
        .filter(Boolean)
        .map((t) => t.slice(0, 140));
    // Newest last in the transcript, so take from the end.
    return Array.from(new Set(texts)).slice(-limit);
}

/** Wipe every cached transcript, returning how many keys went. */
function purgeLocalTranscripts(): number {
    if (typeof window === 'undefined') return 0;
    try {
        const doomed: string[] = [];
        for (let i = 0; i < window.localStorage.length; i += 1) {
            const key = window.localStorage.key(i);
            if (key && key.startsWith(KEY_PREFIX)) doomed.push(key);
        }
        // Removes both the transcripts and their `__session_id` siblings, so the
        // next conversation starts on an id this user owns.
        doomed.forEach((key) => window.localStorage.removeItem(key));
        return doomed.length;
    } catch {
        /* storage disabled — nothing cached to purge */
        return 0;
    }
}

/**
 * Establish that the local cache belongs to `email`, wiping it if it doesn't.
 *
 * `localStorage` is per-browser, not per-user. Without this, the next person to
 * sign in on a shared machine would see the previous user's conversation — and,
 * worse, their first message would save that whole transcript to the server
 * under *their own* account, where it would persist, sync to their other
 * devices, and feed the agent's context. An absent stamp is treated as a
 * mismatch: a cache we can't attribute is one we shouldn't hand to anybody.
 *
 * Returns true only when a transcript was actually discarded, so the caller can
 * reset a view that is already showing it. A first-ever load has nothing cached
 * and so reports false.
 */
export function reconcileCacheOwner(email: string | null | undefined): boolean {
    const owner = (email || '').trim().toLowerCase();
    if (!owner || typeof window === 'undefined') return false;

    let purged = false;
    try {
        if (window.localStorage.getItem(OWNER_KEY) !== owner) {
            purged = purgeLocalTranscripts() > 0;
            window.localStorage.setItem(OWNER_KEY, owner);
        }
    } catch {
        /* storage disabled: nothing is cached, so there is nothing to mix up */
    }
    confirmedOwner = owner;
    if (purged) {
        // Anything queued was composed against the previous user's transcript.
        pendingWrites.forEach(({ timer }) => clearTimeout(timer));
        pendingWrites.clear();
        hydrated.clear();
    }
    return purged;
}

/** Forget the transcript everywhere and start a new session. */
export function discard(persistKey: string): void {
    if (!persistKey) return;
    cancelSave(persistKey);
    clearCache(persistKey);
    hydrated.delete(persistKey);

    const sessionId = getSessionId(persistKey);
    // Rotate first: a failed delete must not leave the next conversation writing
    // into the transcript the user just cleared.
    rotateSessionId(persistKey);
    if (sessionId) {
        void api.deleteChatSession(sessionId).catch(() => {
            /* best effort; retention pruning will collect it */
        });
    }
}
