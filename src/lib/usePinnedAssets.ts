/**
 * Server-side pins of individual Discover assets (tables, views, metric views,
 * dashboards, apps, legacy dashboards), shared by every component on the page.
 *
 * One module-level store: the first consumer fetches, everyone else subscribes,
 * so a pin toggled on a card shows up in the strip and the detail drawer at
 * once without prop drilling. Mutations are optimistic and roll back on error.
 *
 * Separate from `usePinnedItems` (browser-only pins of data products on the
 * chat home page).
 */
import { useCallback, useEffect, useSyncExternalStore } from 'react';
import { api } from '../services/api';
import type { DataAsset, PinnedAsset, PinnedAssetInput } from '../services/api';

interface PinState {
  pins: PinnedAsset[];
  /** True until the first fetch settles. */
  loading: boolean;
  /** Last mutation or load error, cleared by the next success. */
  error: string | null;
  max: number;
}

const STALE_MS = 60_000;

/** `asset_type` of a pinned legacy dashboard mapping; it opens its replacing metric view. */
export const LEGACY_DASHBOARD_PIN_TYPE = 'legacy_dashboard';

let state: PinState = { pins: [], loading: true, error: null, max: 50 };
let loadedAt = 0;
let inflight: Promise<void> | null = null;
const listeners = new Set<() => void>();
// Bumped on every mutation of an asset so a slow response for an older toggle
// can't overwrite (or roll back) a newer one.
const versions = new Map<string, number>();

function setState(patch: Partial<PinState>) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}

function load(force = false): Promise<void> {
  if (inflight) return inflight;
  if (!force && loadedAt && Date.now() - loadedAt < STALE_MS) return Promise.resolve();
  inflight = api
    .listPins()
    .then((res) => {
      loadedAt = Date.now();
      setState({ pins: res.pins, max: res.max, loading: false, error: null });
    })
    .catch((e: unknown) => {
      setState({ loading: false, error: e instanceof Error ? e.message : 'Failed to load pins' });
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

function onVisible() {
  if (document.visibilityState === 'visible') void load();
}

function subscribe(listener: () => void) {
  if (listeners.size === 0 && typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisible);
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', onVisible);
    }
  };
}

const getSnapshot = () => state;

// Requests for one asset run in order, so a quick pin-then-unpin can't reach
// the server reversed and leave it pinned while the UI shows it unpinned.
const queues = new Map<string, Promise<unknown>>();

function enqueue<T>(assetId: string, request: () => Promise<T>): Promise<T> {
  const run = (queues.get(assetId) ?? Promise.resolve()).catch(() => {}).then(request);
  queues.set(assetId, run);
  void run.catch(() => {}).finally(() => {
    if (queues.get(assetId) === run) queues.delete(assetId);
  });
  return run;
}

function bump(assetId: string): number {
  const v = (versions.get(assetId) ?? 0) + 1;
  versions.set(assetId, v);
  return v;
}

/** Put `pin` back (or remove the asset when `pin` is undefined), keeping order. */
function restore(assetId: string, pin: PinnedAsset | undefined, index: number) {
  const without = state.pins.filter((p) => p.asset_id !== assetId);
  if (pin) without.splice(Math.min(index, without.length), 0, pin);
  return without;
}

async function pin(input: PinnedAssetInput): Promise<void> {
  const prevIndex = state.pins.findIndex((p) => p.asset_id === input.asset_id);
  const prev = prevIndex >= 0 ? state.pins[prevIndex] : undefined;
  if (!prev && state.pins.length >= state.max) {
    const message = `You can pin up to ${state.max} assets. Unpin one before pinning another.`;
    setState({ error: message });
    throw new Error(message);
  }
  const v = bump(input.asset_id);
  const optimistic: PinnedAsset = {
    subtitle: null,
    url: null,
    ...input,
    created_at: prev?.created_at ?? new Date().toISOString(),
    available: prev?.available ?? true,
  };
  setState({
    pins: prev
      ? state.pins.map((p) => (p.asset_id === input.asset_id ? optimistic : p))
      : [optimistic, ...state.pins],
  });
  try {
    const saved = await enqueue(input.asset_id, () => api.pinAsset(input));
    if (versions.get(input.asset_id) !== v) return;
    setState({ pins: state.pins.map((p) => (p.asset_id === input.asset_id ? saved : p)), error: null });
  } catch (e) {
    if (versions.get(input.asset_id) === v) {
      setState({
        pins: restore(input.asset_id, prev, prevIndex),
        error: e instanceof Error ? e.message : 'Failed to pin',
      });
    }
    throw e;
  }
}

async function unpin(assetId: string): Promise<void> {
  const prevIndex = state.pins.findIndex((p) => p.asset_id === assetId);
  if (prevIndex < 0) return;
  const prev = state.pins[prevIndex];
  const v = bump(assetId);
  setState({ pins: state.pins.filter((p) => p.asset_id !== assetId) });
  try {
    await enqueue(assetId, () => api.unpinAsset(assetId));
    if (versions.get(assetId) === v) setState({ error: null });
  } catch (e) {
    if (versions.get(assetId) === v) {
      setState({
        pins: restore(assetId, prev, prevIndex),
        error: e instanceof Error ? e.message : 'Failed to unpin',
      });
    }
    throw e;
  }
}

/**
 * Build the pin payload for a catalog asset. Metric views are labelled by
 * where they sit in the business hierarchy; everything else by its schema.
 */
export function pinInputFromAsset(
  asset: Pick<DataAsset, 'id' | 'type' | 'table_name' | 'catalog' | 'schema_name'> &
    Partial<Pick<DataAsset, 'domain' | 'subdomain'>> & { url?: string | null },
): PinnedAssetInput {
  const type = String(asset.type || 'table').toLowerCase();
  const hierarchy = [asset.domain, asset.subdomain].filter(Boolean).join(' › ');
  const schema = [asset.catalog, asset.schema_name].filter(Boolean).join('.');
  return {
    asset_id: asset.id,
    asset_type: type,
    title: asset.table_name || asset.id,
    subtitle: (type === 'metric_view' && hierarchy) || schema || hierarchy || null,
    url: asset.url && /^https?:\/\//i.test(asset.url) ? asset.url : null,
  };
}

export function usePinnedAssets() {
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    void load();
  }, []);

  const isPinned = useCallback(
    (assetId: string) => snapshot.pins.some((p) => p.asset_id === assetId),
    [snapshot.pins],
  );

  const togglePin = useCallback(
    (asset: PinnedAssetInput) =>
      state.pins.some((p) => p.asset_id === asset.asset_id) ? unpin(asset.asset_id) : pin(asset),
    [],
  );

  return {
    pins: snapshot.pins,
    loading: snapshot.loading,
    error: snapshot.error,
    max: snapshot.max,
    isPinned,
    togglePin,
    pin,
    unpin,
    refresh: () => load(true),
  };
}
