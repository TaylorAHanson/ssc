/**
 * `usePendingPoll` — drives the poll loop after a `pending_poll` event.
 *
 * Currently scoped to Genie polls (only async backend in v1), but the
 * interface stays kind-agnostic so that future MCP-backed async tools
 * can plug in by extending the dispatch table.
 */
import { useEffect, useRef, useState } from 'react';

import {
    pollGenieResponse,
    type GeniePollResponse,
    type PendingPollEvent,
} from '../lib/agentStream';

export interface PendingPollState {
    /** Live status of the poll cycle. */
    status: 'idle' | 'running' | 'complete' | 'failed' | 'cancelled' | 'timeout';
    /** Resolved result (when status === 'complete'). */
    result: Record<string, unknown> | null;
    /** Failure description (status === 'failed' | 'timeout'). */
    error: string | null;
    /** Wall-clock elapsed milliseconds since the poll started. */
    elapsedMs: number;
    cancel: () => void;
}

export interface UsePendingPollOptions {
    /** Hard cap on the total polling window. Defaults to 180s. */
    timeoutMs?: number;
    /** Initial poll interval; the server may override per-request. */
    intervalMs?: number;
    /**
     * Called once when the poll resolves, fails, times out, or is
     * cancelled. The originating `pollEvent` is passed alongside the
     * state so the consumer can identify which pending pill the
     * resolution belongs to — important when a new turn replaces an
     * older still-running poll, since the closure-captured "current"
     * poll has already changed by the time cleanup runs.
     */
    onSettled?: (state: PendingPollState, pollEvent: PendingPollEvent) => void;
}

const DEFAULT_TIMEOUT_MS = 180_000;
const DEFAULT_INTERVAL_MS = 3_000;

/**
 * Watch a `pending_poll` event and drain it until completion.
 *
 * Pass `null` to disable polling (e.g. when no poll is active).
 * Switching the event prop to a different `tool_call_id` cancels the
 * previous loop and starts a fresh one.
 */
export function usePendingPoll(
    pollEvent: PendingPollEvent | null,
    options: UsePendingPollOptions = {},
): PendingPollState {
    const { timeoutMs = DEFAULT_TIMEOUT_MS, intervalMs = DEFAULT_INTERVAL_MS, onSettled } = options;

    const [state, setState] = useState<PendingPollState>({
        status: 'idle',
        result: null,
        error: null,
        elapsedMs: 0,
        cancel: () => {},
    });

    // Latest onSettled in a ref so it doesn't restart the loop on
    // every render.
    const onSettledRef = useRef(onSettled);
    onSettledRef.current = onSettled;

    useEffect(() => {
        if (!pollEvent) {
            setState((prev) =>
                prev.status === 'idle' ? prev : { ...prev, status: 'idle' },
            );
            return undefined;
        }

        if (pollEvent.kind !== 'genie') {
            // Future kinds plug in here; for now anything we don't
            // recognize is surfaced as an error so the chat loop
            // doesn't hang waiting on an event we can't drain.
            const failureState: PendingPollState = {
                status: 'failed',
                result: null,
                error: `Unknown pending_poll kind: ${pollEvent.kind}`,
                elapsedMs: 0,
                cancel: () => {},
            };
            setState(failureState);
            onSettledRef.current?.(failureState, pollEvent);
            return undefined;
        }

        const controller = new AbortController();
        const startedAt = Date.now();
        let cancelled = false;
        let elapsedTimer: ReturnType<typeof setInterval> | null = null;
        let nextPollTimeout: ReturnType<typeof setTimeout> | null = null;

        const cancel = () => {
            if (cancelled) return;
            cancelled = true;
            controller.abort();
            if (elapsedTimer) clearInterval(elapsedTimer);
            if (nextPollTimeout) clearTimeout(nextPollTimeout);
            setState((prev) => {
                const next: PendingPollState = {
                    ...prev,
                    status: 'cancelled',
                    cancel: () => {},
                };
                onSettledRef.current?.(next, pollEvent);
                return next;
            });
        };

        setState({
            status: 'running',
            result: null,
            error: null,
            elapsedMs: 0,
            cancel,
        });

        // Tick the elapsed counter every second so the UI can render
        // a live "Asking Genie (32s)..." label without re-driving the
        // poll itself.
        elapsedTimer = setInterval(() => {
            if (cancelled) return;
            setState((prev) => ({ ...prev, elapsedMs: Date.now() - startedAt }));
        }, 1000);

        const settle = (next: PendingPollState) => {
            if (cancelled) return;
            cancelled = true;
            if (elapsedTimer) clearInterval(elapsedTimer);
            if (nextPollTimeout) clearTimeout(nextPollTimeout);
            setState(next);
            onSettledRef.current?.(next, pollEvent);
        };

        const tick = async () => {
            if (cancelled) return;
            if (Date.now() - startedAt >= timeoutMs) {
                settle({
                    status: 'timeout',
                    result: null,
                    error: `Genie did not respond within ${Math.round(timeoutMs / 1000)}s.`,
                    elapsedMs: Date.now() - startedAt,
                    cancel: () => {},
                });
                return;
            }

            let response: GeniePollResponse;
            try {
                // space_id is optional — empty / null means general
                // Databricks Genie (search across all accessible data).
                // Only pass it through when the pending_poll event
                // actually carried one (i.e. a space-pinned call).
                const spaceId = pollEvent.ids.space_id
                    ? String(pollEvent.ids.space_id)
                    : undefined;
                response = await pollGenieResponse(
                    {
                        ...(spaceId ? { space_id: spaceId } : {}),
                        conversation_id: String(pollEvent.ids.conversation_id ?? ''),
                        message_id: String(pollEvent.ids.message_id ?? ''),
                        question: pollEvent.ids.question
                            ? String(pollEvent.ids.question)
                            : undefined,
                    },
                    { signal: controller.signal },
                );
            } catch (err) {
                if (cancelled || controller.signal.aborted) return;
                settle({
                    status: 'failed',
                    result: null,
                    error: err instanceof Error ? err.message : String(err),
                    elapsedMs: Date.now() - startedAt,
                    cancel: () => {},
                });
                return;
            }

            if (cancelled) return;

            if (response.status === 'complete') {
                settle({
                    status: 'complete',
                    result: response.result ?? null,
                    error: null,
                    elapsedMs: Date.now() - startedAt,
                    cancel: () => {},
                });
                return;
            }
            if (response.status === 'failed') {
                settle({
                    status: 'failed',
                    result: null,
                    error: response.error ?? 'Genie reported a failure.',
                    elapsedMs: Date.now() - startedAt,
                    cancel: () => {},
                });
                return;
            }

            // Still running. Schedule the next poll.
            const wait = response.attempt_after_ms ?? intervalMs;
            nextPollTimeout = setTimeout(() => {
                void tick();
            }, wait);
        };

        // Kick off immediately - users notice a blank interval if we
        // wait the full intervalMs before the first poll.
        nextPollTimeout = setTimeout(() => {
            void tick();
        }, 0);

        return cancel;
        // The poll identity for restarting the loop is the tool_call_id
        // of the originating tool call; everything else is captured.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pollEvent?.tool_call_id]);

    return state;
}
