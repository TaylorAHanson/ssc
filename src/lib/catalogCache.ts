/**
 * In-memory, stale-while-revalidate cache for the data catalog.
 *
 * The Discover page assembles its asset list from six separate endpoints,
 * which makes a cold load feel like a blank page for a few seconds. To avoid
 * that, every catalog fetch goes through a small module-level cache here:
 *
 *   - The first consumer triggers the network call.
 *   - Concurrent callers share the same in-flight promise (deduped).
 *   - Later consumers get the cached value *immediately* and a background
 *     revalidation refreshes it when stale.
 *   - The agent landing prefetches the whole catalog (`prefetchCatalog`) so
 *     that clicking "Browse all" into Discover renders instantly.
 *
 * The cache lives for the browser session; it is intentionally simple (no
 * persistence) since the data is cheap to refetch and changes infrequently.
 */
import { useEffect, useState } from 'react';

import { api } from '../services/api';

const TTL_MS = 5 * 60_000;

interface CacheEntry<T> {
  data: T | null;
  ts: number;
  inflight: Promise<T> | null;
}

interface Resource<T> {
  /** Kick off (or reuse) a fetch; resolves with fresh-or-cached data. */
  load: (force?: boolean) => Promise<T>;
  /** Synchronously read whatever is cached, or null. */
  peek: () => T | null;
  /** React hook: serves cached data instantly, revalidates in the background. */
  useResource: () => { data: T; loading: boolean };
}

function createResource<T>(fetcher: () => Promise<T>, fallback: T): Resource<T> {
  const entry: CacheEntry<T> = { data: null, ts: 0, inflight: null };

  const isFresh = () => entry.data !== null && Date.now() - entry.ts < TTL_MS;

  function load(force = false): Promise<T> {
    if (!force && isFresh()) return Promise.resolve(entry.data as T);
    if (entry.inflight) return entry.inflight;
    entry.inflight = fetcher()
      .then((d) => {
        entry.data = d;
        entry.ts = Date.now();
        return d;
      })
      .catch((e) => {
        // Prefer keeping previously cached data over surfacing a transient error.
        if (entry.data !== null) return entry.data;
        throw e;
      })
      .finally(() => {
        entry.inflight = null;
      });
    return entry.inflight;
  }

  function useResource() {
    const [data, setData] = useState<T>(entry.data ?? fallback);
    // Only show a loading state when we have nothing cached to render.
    const [loading, setLoading] = useState<boolean>(entry.data === null);

    useEffect(() => {
      let mounted = true;
      if (entry.data !== null) {
        setData(entry.data);
        setLoading(false);
      }
      load()
        .then((d) => {
          if (mounted) {
            setData(d);
            setLoading(false);
          }
        })
        .catch(() => {
          if (mounted) setLoading(false);
        });
      return () => {
        mounted = false;
      };
    }, []);

    return { data, loading };
  }

  return { load, peek: () => entry.data, useResource };
}

// ---------------------------------------------------------------------------
// Underlying endpoint resources. Non-essential ones swallow errors so a single
// failing integration never blanks the whole catalog.
// ---------------------------------------------------------------------------
export const dataAssetsResource = createResource<any[]>(() => api.getDataAssets(), []);
export const odpsResource = createResource<any[]>(() => api.getOdpsList().catch(() => []), []);
const dashboardsResource = createResource<any[]>(() => api.getDatabricksDashboards().catch(() => []), []);
const jobsResource = createResource<any[]>(() => api.getDatabricksJobs().catch(() => []), []);
const appsResource = createResource<any[]>(() => api.getDatabricksApps().catch(() => []), []);
const genieResource = createResource<any[]>(() => api.getDatabricksGenieSpaces().catch(() => []), []);

/**
 * Merge the raw endpoint payloads into the unified asset list the Discover
 * page renders. Mirrors the shape the page previously built inline.
 */
function combineCatalog(
  data: any[],
  dashboards: any[],
  jobs: any[],
  apps: any[],
  genieSpaces: any[],
  odpsList: any[],
): any[] {
  const mappedData = data.map((d) =>
    d.type === 'DATA_PRODUCT' ? { ...d, type: 'dataset' } : d,
  );

  return [
    ...mappedData,
    ...dashboards.map((d) => ({
      id: d.id,
      table_name: d.name,
      type: 'dashboard',
      domain: 'Analytics',
      description: 'Lakeview Dashboard',
      catalog: 'workspace',
      schema_name: 'dashboards',
      certified: false,
      tags: [],
    })),
    ...jobs.map((j) => ({
      id: j.id,
      table_name: j.name,
      type: 'job',
      domain: 'Engineering',
      description: `Job created by ${j.creator}`,
      catalog: 'workspace',
      schema_name: 'jobs',
      certified: false,
      tags: [],
    })),
    ...apps.map((a) => ({
      id: a.id,
      table_name: a.name,
      type: 'app',
      domain: 'Engineering',
      description: `App created by ${a.creator}`,
      catalog: 'workspace',
      schema_name: 'apps',
      certified: false,
      tags: [],
    })),
    ...genieSpaces.map((g) => ({
      id: g.id,
      table_name: g.name,
      type: 'genie_space',
      domain: 'Analytics',
      description: g.description || 'Genie Space',
      catalog: 'workspace',
      schema_name: 'genie',
      certified: false,
      tags: [],
    })),
    ...odpsList.map((o) => ({
      id: o.id,
      table_name: o.name,
      type: 'data_product',
      domain: 'Analytics',
      description: 'ODPS Data Product',
      catalog: 'workspace',
      schema_name: 'odps',
      certified: false,
      tags: [],
    })),
  ];
}

export const discoveryCatalogResource = createResource<any[]>(async () => {
  const [data, dashboards, jobs, apps, genieSpaces, odpsList] = await Promise.all([
    dataAssetsResource.load(),
    dashboardsResource.load(),
    jobsResource.load(),
    appsResource.load(),
    genieResource.load(),
    odpsResource.load(),
  ]);
  return combineCatalog(data, dashboards, jobs, apps, genieSpaces, odpsList);
}, []);

/** Hook for the unified Discover catalog. */
export const useDiscoveryCatalog = discoveryCatalogResource.useResource;

/**
 * The set of asset IDs the current user can actually access, computed server
 * side against Unity Catalog (OBO). `available` is false when real entitlement
 * data can't be computed (e.g. local dev) — callers should then hide the
 * "Accessible to me" filter rather than guess.
 */
export const accessibleAssetsResource = createResource<{
  available: boolean;
  mode: string;
  accessible_ids: string[];
}>(() => api.getAccessibleAssetIds(), { available: false, mode: 'unavailable', accessible_ids: [] });

/** Hook for the current user's accessible-asset set. */
export const useAccessibleAssets = accessibleAssetsResource.useResource;

/**
 * Warm the cache ahead of navigation. Safe to call repeatedly; concurrent
 * calls dedupe and a fresh cache is a no-op. Errors are swallowed — this is
 * purely an optimization.
 */
export function prefetchCatalog(): void {
  discoveryCatalogResource.load().catch(() => {});
}
