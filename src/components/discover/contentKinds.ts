import { TrendingUp, Database, LayoutDashboard, Box, ArrowLeftRight } from 'lucide-react';
import type { DataAsset, LegacyMapping } from '../../services/api';
import type { DiscoverContentKind } from '../../lib/discoverLinks';

export type ContentKind = DiscoverContentKind;

export const CONTENT_KINDS: Array<{
  key: ContentKind;
  label: string;
  Icon: typeof TrendingUp;
  /** Classes when selected / accent when not. */
  on: string;
  off: string;
}> = [
  { key: 'metrics', label: 'Metric Views', Icon: TrendingUp, on: 'bg-primary/10 border-primary text-primary', off: 'text-primary' },
  { key: 'tables', label: 'Tables & Views', Icon: Database, on: 'bg-indigo-50 border-indigo-600 text-indigo-800', off: 'text-indigo-700' },
  { key: 'dashboards', label: 'Dashboards', Icon: LayoutDashboard, on: 'bg-amber-50 border-amber-500 text-amber-800', off: 'text-amber-700' },
  { key: 'apps', label: 'Apps', Icon: Box, on: 'bg-purple-50 border-purple-600 text-purple-800', off: 'text-purple-700' },
  // Only offered where admins have mapped legacy (e.g. Tableau) dashboards.
  { key: 'legacy', label: 'Legacy Dashboards', Icon: ArrowLeftRight, on: 'bg-slate-100 border-slate-700 text-slate-800', off: 'text-slate-600' },
];

/** Per-kind item counts; a missing kind means its data hasn't loaded yet. */
export type ContentKindCounts = Partial<Record<ContentKind, number>>;

/**
 * What a whole-domain view would list per kind before any filters, using the
 * same rules as the Discover page: dashboards come from the lineage of the
 * domain's metric views, and apps also include those tagged Engineering.
 */
export function countDomainContent(
  domain: string,
  sources: {
    metricViews: DataAsset[] | null;
    /** The Discover catalog; apps there may only carry `name`. */
    assets: Array<DataAsset & { name?: string }> | null;
    legacyMappings: LegacyMapping[] | null;
  },
): ContentKindCounts {
  const counts: ContentKindCounts = {};
  const { metricViews, assets, legacyMappings } = sources;
  const domainViews = metricViews?.filter((mv) => mv.domain === domain) ?? null;

  if (domainViews) counts.metrics = domainViews.length;
  if (assets) counts.tables = assets.filter((a) => a.domain === domain && a.type !== 'METRIC_VIEW').length;
  if (legacyMappings) counts.legacy = legacyMappings.filter((m) => m.domain === domain).length;

  if (domainViews) {
    const dashboards = new Set<string>();
    const lineageApps = new Set<string>();
    for (const mv of domainViews) {
      for (const d of mv.downstream_dashboards ?? []) {
        if (!d?.name) continue;
        (d.type === 'app' ? lineageApps : dashboards).add(d.name);
      }
    }
    counts.dashboards = dashboards.size;
    if (assets) {
      const apps = assets.filter((a) => a.type === 'app' && (a.domain === domain || a.domain === 'Engineering'));
      const appNames = new Set(apps.map((a) => a.table_name || a.name));
      counts.apps = apps.length + [...lineageApps].filter((n) => !appNames.has(n)).length;
    }
  }
  return counts;
}
