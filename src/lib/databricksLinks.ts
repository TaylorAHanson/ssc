/**
 * Helpers to deep-link from the app into a Databricks workspace
 * (Catalog Explorer, Dashboards, Jobs, Apps, Genie).
 *
 * The workspace base URL is exposed by the backend `/branding` endpoint
 * and stored in `useBrandingStore.databricksWorkspaceUrl` as a clean
 * `https://<host>` value with no trailing slash.
 */

function trimSlash(s: string): string {
    return s.replace(/\/+$/, '');
}

function ensureScheme(host: string): string {
    if (!host) return '';
    return /^https?:\/\//i.test(host) ? host : `https://${host}`;
}

/** Normalize a workspace URL passed in from settings/branding. */
export function normalizeWorkspaceUrl(raw: string | null | undefined): string {
    if (!raw) return '';
    return trimSlash(ensureScheme(raw.trim()));
}

/** Catalog Explorer URL for a catalog / schema / table (any may be omitted). */
export function catalogExplorerUrl(
    workspaceUrl: string,
    catalog?: string | null,
    schema?: string | null,
    table?: string | null,
): string | null {
    const base = normalizeWorkspaceUrl(workspaceUrl);
    if (!base || !catalog) return null;
    const parts = [encodeURIComponent(catalog)];
    if (schema) parts.push(encodeURIComponent(schema));
    if (table) parts.push(encodeURIComponent(table));
    return `${base}/explore/data/${parts.join('/')}`;
}

/** Lakeview / AI-BI dashboard published view URL. */
export function dashboardUrl(workspaceUrl: string, dashboardId: string): string | null {
    const base = normalizeWorkspaceUrl(workspaceUrl);
    if (!base || !dashboardId) return null;
    return `${base}/dashboardsv3/${encodeURIComponent(dashboardId)}/published`;
}

/** Workflows / Jobs detail URL. */
export function jobUrl(workspaceUrl: string, jobId: string | number): string | null {
    const base = normalizeWorkspaceUrl(workspaceUrl);
    if (!base || jobId === undefined || jobId === null || jobId === '') return null;
    return `${base}/jobs/${encodeURIComponent(String(jobId))}`;
}

/** Databricks Apps detail URL (uses the app name). */
export function appUrl(workspaceUrl: string, appName: string): string | null {
    const base = normalizeWorkspaceUrl(workspaceUrl);
    if (!base || !appName) return null;
    return `${base}/apps/${encodeURIComponent(appName)}`;
}

/** Genie space (room) URL. */
export function genieSpaceUrl(workspaceUrl: string, spaceId: string): string | null {
    const base = normalizeWorkspaceUrl(workspaceUrl);
    if (!base || !spaceId) return null;
    return `${base}/genie/rooms/${encodeURIComponent(spaceId)}`;
}

/**
 * Best-effort URL builder for an asset rendered in the Discover page.
 * Returns `null` when no sensible Databricks deep link is available
 * (e.g. dataset without a contract — caller should fall back to the
 * per-table link from the contract instead).
 */
export function assetWorkspaceUrl(
    workspaceUrl: string,
    asset: {
        type?: string | null;
        catalog?: string | null;
        schema_name?: string | null;
        table_name?: string | null;
        id?: string | null;
    },
): string | null {
    if (!asset) return null;
    const t = String(asset.type || '').toLowerCase();

    if (t === 'managed' || t === 'external' || t === 'view') {
        return catalogExplorerUrl(workspaceUrl, asset.catalog, asset.schema_name, asset.table_name);
    }
    if (t === 'dashboard') {
        return dashboardUrl(workspaceUrl, asset.id || '');
    }
    if (t === 'job') {
        return jobUrl(workspaceUrl, asset.id || '');
    }
    if (t === 'app') {
        // For apps the readable name is what the URL needs.
        return appUrl(workspaceUrl, asset.table_name || asset.id || '');
    }
    if (t === 'genie_space') {
        return genieSpaceUrl(workspaceUrl, asset.id || '');
    }
    // Datasets / data products don't have a single page; caller should
    // surface per-table links from the contract servers.
    return null;
}

/** Human-readable label for the link, given an asset type. */
export function workspaceLinkLabel(assetType?: string | null): string {
    const t = String(assetType || '').toLowerCase();
    switch (t) {
        case 'managed':
        case 'external':
        case 'view':
            return 'Open in Catalog Explorer';
        case 'dashboard':
            return 'Open Dashboard';
        case 'job':
            return 'Open Job';
        case 'app':
            return 'Open App';
        case 'genie_space':
            return 'Open Genie Space';
        default:
            return 'Open in Databricks';
    }
}
