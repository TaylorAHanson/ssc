/**
 * Per-user "pinned items" persistence.
 *
 * Lets a user pin catalog assets (data products, datasets, …) so they surface
 * in a "Your Pinned Items" rail for quick access. Pins are stored in
 * localStorage, namespaced by the user so different accounts on the same
 * browser don't share pins. We persist the minimal display fields alongside
 * the key so the pinned rail can render without re-fetching the source list.
 */
import { useCallback, useEffect, useState } from 'react';
import { normalizeAssetType } from './assetTypes';

export interface PinnedItem {
  /** Stable identity: `${normalizedType}:${id}`. */
  key: string;
  id: string;
  type: string;
  title: string;
  subtitle?: string | null;
  certified?: boolean;
}

/** Build the stable pin key for an asset. */
export function pinKey(type: string | null | undefined, id: string): string {
  return `${normalizeAssetType(type)}:${id}`;
}

function storageKeyFor(scope?: string): string {
  return `pinned_items_${scope || 'default'}`;
}

function load(scope?: string): PinnedItem[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(storageKeyFor(scope));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as PinnedItem[]) : [];
  } catch {
    return [];
  }
}

export function usePinnedItems(scope?: string) {
  const [pinned, setPinned] = useState<PinnedItem[]>(() => load(scope));

  // Reload when the scope (user) changes so pins follow the active account.
  useEffect(() => {
    setPinned(load(scope));
  }, [scope]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(storageKeyFor(scope), JSON.stringify(pinned));
    } catch {
      /* storage disabled / full — non-fatal */
    }
  }, [pinned, scope]);

  const isPinned = useCallback(
    (key: string) => pinned.some((p) => p.key === key),
    [pinned],
  );

  const togglePin = useCallback((item: PinnedItem) => {
    setPinned((prev) =>
      prev.some((p) => p.key === item.key)
        ? prev.filter((p) => p.key !== item.key)
        : [...prev, item],
    );
  }, []);

  const unpin = useCallback((key: string) => {
    setPinned((prev) => prev.filter((p) => p.key !== key));
  }, []);

  return { pinned, isPinned, togglePin, unpin };
}
