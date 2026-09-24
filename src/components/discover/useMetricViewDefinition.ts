import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../../services/api';
import type { MetricViewDefinition } from '../../services/api';

// Metric view definitions (KPIs, source tables) are read as the user, one warehouse
// statement per view. Cards that mount together ask within the same tick, so their
// ids are collected and sent as one batch request; results are kept for the page
// session so re-renders, pagination back-and-forth and the detail panel reuse them.
const BATCH_DELAY_MS = 25;
const BATCH_SIZE = 25;

const definitions = new Map<string, MetricViewDefinition>();
const requested = new Set<string>();
const listeners = new Set<() => void>();
let queue: string[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function notify() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function failed(error: string): MetricViewDefinition {
  return { available: false, reason: 'error', kpis: [], upstream_tables: [], error };
}

function flush() {
  flushTimer = null;
  const ids = queue;
  queue = [];
  for (let i = 0; i < ids.length; i += BATCH_SIZE) {
    const chunk = ids.slice(i, i + BATCH_SIZE);
    api
      .getMetricViewDefinitions(chunk)
      .then((result) => chunk.forEach((id) => definitions.set(id, result[id] ?? failed('Not returned'))))
      .catch((e: Error) => chunk.forEach((id) => definitions.set(id, failed(e.message))))
      .finally(notify);
  }
}

function request(assetId: string) {
  if (requested.has(assetId)) return;
  requested.add(assetId);
  queue.push(assetId);
  if (!flushTimer) flushTimer = setTimeout(flush, BATCH_DELAY_MS);
}

/** Record a definition read elsewhere (e.g. by the detail panel) so cards show it too. */
export function primeMetricViewDefinition(assetId: string, definition: MetricViewDefinition) {
  requested.add(assetId);
  definitions.set(assetId, definition);
  notify();
}

/** The metric view's definition as the current user sees it; undefined while loading. */
export function useMetricViewDefinition(assetId: string): MetricViewDefinition | undefined {
  useEffect(() => request(assetId), [assetId]);
  return useSyncExternalStore(subscribe, () => definitions.get(assetId));
}
