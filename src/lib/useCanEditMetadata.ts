import { useEffect, useState } from 'react';
import { getWhoAmI } from '../services/metadataApi';
import { useBrandingStore } from '../stores/brandingStore';
import { useUserStore } from '../stores/userStore';

// One whoami call per signed-in user, shared by every detail panel that asks.
const cache = new Map<string, Promise<boolean>>();

function canBrowse(user: string): Promise<boolean> {
  let hit = cache.get(user);
  if (!hit) {
    hit = getWhoAmI()
      .then((w) => w.can_browse)
      .catch(() => {
        // Don't remember a failure: the next panel that opens tries again.
        cache.delete(user);
        return false;
      });
    cache.set(user, hit);
  }
  return hit;
}

/**
 * Whether to offer "Edit" links into the Metadata Manager: the page is switched on and the
 * backend says this user can browse it (governance, or an owner of at least one object).
 * The page itself still limits an owner to the objects they own.
 */
export function useCanEditMetadata(): boolean {
  const tabOn = useBrandingStore((s) => s.uiTabs?.tag_management !== false);
  const user = useUserStore((s) => s.currentUser?.email ?? '');
  const [allowed, setAllowed] = useState<{ user: string; ok: boolean } | null>(null);

  useEffect(() => {
    if (!tabOn || !user) return;
    let cancelled = false;
    void canBrowse(user).then((ok) => {
      if (!cancelled) setAllowed({ user, ok });
    });
    return () => {
      cancelled = true;
    };
  }, [tabOn, user]);

  return tabOn && !!user && allowed?.user === user && allowed.ok;
}
