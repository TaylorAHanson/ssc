import { useRef, useState } from 'react';
import { MetadataBlockedError, MetadataConflictError } from '../../../services/metadataApi';
import type { ChangePreview, ChangeResult, VersionConflictItem } from '../../../types/metadata';
import { errorMessage, newIdempotencyKey } from './format';

export type ReviewState<R> =
  | { step: 'idle' }
  | { step: 'previewing'; request: R }
  | { step: 'preview'; request: R; preview: ChangePreview; idempotencyKey: string; submitting?: boolean; error?: string }
  | { step: 'result'; request: R; result: ChangeResult }
  | { step: 'conflict'; request: R; message: string; conflicts: VersionConflictItem[] }
  | { step: 'blocked'; request: R; message: string; preview: ChangePreview }
  | { step: 'error'; request: R; message: string };

interface ReviewFns<R> {
  preview: (request: R) => Promise<ChangePreview>;
  submit: (request: R, idempotencyKey: string) => Promise<ChangeResult>;
}

export interface ReviewFlow<R> {
  state: ReviewState<R>;
  start: (request: R) => Promise<void>;
  submit: (request: R) => Promise<void>;
  reset: () => void;
}

/**
 * Preview, then submit the same request. Each preview gets a fresh idempotency key that a
 * retried submit reuses, so a submit that timed out can be retried without saving twice.
 */
export function useReviewFlow<R>(fns: ReviewFns<R>): ReviewFlow<R> {
  const [state, setState] = useState<ReviewState<R>>({ step: 'idle' });
  const seq = useRef(0);

  async function start(request: R) {
    const id = ++seq.current;
    setState({ step: 'previewing', request });
    try {
      const preview = await fns.preview(request);
      if (id === seq.current) setState({ step: 'preview', request, preview, idempotencyKey: newIdempotencyKey() });
    } catch (e) {
      if (id !== seq.current) return;
      setState(failure(request, e));
    }
  }

  async function submit(request: R) {
    if (state.step !== 'preview' || state.submitting) return;
    const { preview, idempotencyKey } = state;
    const id = ++seq.current;
    setState({ ...state, request, submitting: true, error: undefined });
    try {
      const result = await fns.submit(request, idempotencyKey);
      if (id === seq.current) setState({ step: 'result', request, result });
    } catch (e) {
      if (id !== seq.current) return;
      if (e instanceof MetadataConflictError || e instanceof MetadataBlockedError) setState(failure(request, e));
      else setState({ step: 'preview', request, preview, idempotencyKey, error: errorMessage(e, 'Couldn’t submit. Try again.') });
    }
  }

  function reset() {
    seq.current++;
    setState({ step: 'idle' });
  }

  return { state, start, submit, reset };
}

function failure<R>(request: R, e: unknown): ReviewState<R> {
  if (e instanceof MetadataConflictError) return { step: 'conflict', request, message: e.message, conflicts: e.conflicts };
  if (e instanceof MetadataBlockedError) return { step: 'blocked', request, message: e.message, preview: e.preview };
  return { step: 'error', request, message: errorMessage(e) };
}
