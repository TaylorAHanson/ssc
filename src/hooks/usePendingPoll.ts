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
    /**
     * Latest in-progress snapshot while running (the enriched Genie payload).
     * Genie re-sends the full answer each poll and it can change
     * non-additively, so consumers must RENDER BY REPLACING this each tick.
     */
    partialResult: Record<string, unknown> | null;
    /** Convenience: the answer text pulled from `partialResult` (or result). */
    partialAnswer: string | null;
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

// Genie's terminal status can lag well past when the answer is actually ready
// (the native Databricks UI shows it streaming and "done" long before our poll
// sees a COMPLETED status). To avoid spinning until the hard timeout, we treat
// a non-empty answer that hasn't changed across this many consecutive polls as
// done. At the default 3s cadence that's ~15s of a stable answer — long enough
// that we don't grab a pre-final snapshot during normal streaming, short enough
// to rescue the customer-env case where the status flip never arrives in time.
const STABLE_POLLS_TO_COMPLETE = 5;

/**
 * Pull the human-readable text out of a Genie payload snapshot.
 *
 * Genie keeps `final_answer` empty until the very end and streams its work as
 * `progress_steps` (which the backend normalizes into `_stream_narration`). So
 * during streaming we surface the narration; once the real answer lands we
 * prefer that.
 */
function extractGenieAnswer(
    obj: Record<string, unknown> | null | undefined,
): string | null {
    if (!obj || typeof obj !== 'object') return null;
    const fa = obj.final_answer;
    if (typeof fa === 'string' && fa.trim()) return fa;
    const narration = obj._stream_narration;
    if (typeof narration === 'string' && narration.trim()) return narration;
    const txt = obj.text;
    if (typeof txt === 'string' && txt.trim()) return txt;
    return null;
}

/**
 * The *real* answer only — used as the completion signal. Unlike
 * `extractGenieAnswer` this ignores the streaming narration, so early
 * completion never fires while `final_answer` is still empty (which would
 * settle the turn with narration instead of the actual answer).
 */
function extractFinalAnswer(
    obj: Record<string, unknown> | null | undefined,
): string | null {
    if (!obj || typeof obj !== 'object') return null;
    const fa = obj.final_answer;
    if (typeof fa === 'string' && fa.trim()) return fa;
    const txt = obj.text;
    if (typeof txt === 'string' && txt.trim()) return txt;
    return null;
}

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
        partialResult: null,
        partialAnswer: null,
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
                partialResult: null,
                partialAnswer: null,
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
        // Stability tracking for early completion (see STABLE_POLLS_TO_COMPLETE).
        let lastAnswer: string | null = null;
        let stableCount = 0;

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
            partialResult: null,
            partialAnswer: null,
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

        const settle = (patch: Partial<PendingPollState>) => {
            if (cancelled) return;
            cancelled = true;
            if (elapsedTimer) clearInterval(elapsedTimer);
            if (nextPollTimeout) clearTimeout(nextPollTimeout);
            setState((prev) => {
                const next: PendingPollState = { ...prev, ...patch, cancel: () => {} };
                onSettledRef.current?.(next, pollEvent);
                return next;
            });
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
                const result = response.result ?? null;
                settle({
                    status: 'complete',
                    result,
                    partialResult: result,
                    partialAnswer: extractGenieAnswer(result),
                    error: null,
                    elapsedMs: Date.now() - startedAt,
                });
                return;
            }
            if (response.status === 'failed') {
                settle({
                    status: 'failed',
                    result: null,
                    error: response.error ?? 'Genie reported a failure.',
                    elapsedMs: Date.now() - startedAt,
                });
                return;
            }

            // Still running. Surface the streamed snapshot live so the UI can
            // render the answer as it forms (REPLACING each poll — Genie's
            // answer can change non-additively, so we never append deltas).
            const partial = response.partial ?? null;
            const answer = extractGenieAnswer(partial);
            setState((prev) => ({
                ...prev,
                partialResult: partial,
                partialAnswer: answer ?? prev.partialAnswer,
                elapsedMs: Date.now() - startedAt,
            }));

            // Early completion: only once the REAL answer (`final_answer`) is
            // present and has stopped changing for several polls. We never
            // early-complete on the streaming narration, since that would
            // settle the turn before the actual answer exists. This rescues the
            // customer-env case where Genie's terminal status lags long after
            // the answer is ready.
            const finalAnswer = extractFinalAnswer(partial);
            if (finalAnswer) {
                if (finalAnswer === lastAnswer) {
                    stableCount += 1;
                } else {
                    lastAnswer = finalAnswer;
                    stableCount = 0;
                }
                if (stableCount >= STABLE_POLLS_TO_COMPLETE) {
                    settle({
                        status: 'complete',
                        result: partial,
                        partialResult: partial,
                        partialAnswer: finalAnswer,
                        error: null,
                        elapsedMs: Date.now() - startedAt,
                    });
                    return;
                }
            }

            // Schedule the next poll.
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
