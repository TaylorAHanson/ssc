/**
 * Shared asset taxonomy.
 *
 * The catalog surfaces several kinds of objects (data products, datasets,
 * tables, dashboards, apps, Genie spaces, jobs). Their copy, icons, colors
 * and ordering used to be duplicated across the Discover page and the agent
 * landing. This module is the single source of truth so both surfaces stay
 * visually cohesive and a business user sees the same vocabulary everywhere.
 * The matching UI pieces (badge, card, explainer) live in `assetTypes.tsx`.
 */
import {
  Box,
  Database,
  Table as TableIcon,
  LayoutDashboard,
  Server,
  Sparkles,
  PlaySquare,
  TrendingUp,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export type AssetTypeId =
  | 'metric_view'
  | 'data_product'
  | 'dataset'
  | 'table'
  | 'dashboard'
  | 'app'
  | 'genie_space'
  | 'job';

export interface AssetTypeMeta {
  id: AssetTypeId;
  /** Singular, business-friendly label. */
  label: string;
  /** Plural label used for section headers and filter pills. */
  plural: string;
  icon: LucideIcon;
  /** One-line explanation aimed at a non-technical business user. */
  description: string;
  /** Tailwind text color for the icon/accent. */
  accentText: string;
  /** Tailwind background for the icon chip. */
  accentBg: string;
  /** Tailwind border used on selected pills / cards. */
  accentBorder: string;
}

/**
 * Canonical display order requested by the team:
 * Metric Views → Data Products → Datasets → Dashboards → Apps → Genie Spaces → (leftovers:
 * Tables & Views, then Jobs).
 */
export const ASSET_TYPE_ORDER: AssetTypeId[] = [
  'metric_view',
  'data_product',
  'dataset',
  'dashboard',
  'app',
  'genie_space',
  'table',
  'job',
];

export const ASSET_TYPES: Record<AssetTypeId, AssetTypeMeta> = {
  metric_view: {
    id: 'metric_view',
    label: 'Metric View',
    plural: 'Metric Views',
    icon: TrendingUp,
    description: 'Governed business metrics, KPIs, and semantic dimensions.',
    accentText: 'text-emerald-700',
    accentBg: 'bg-emerald-50',
    accentBorder: 'border-emerald-200',
  },
  data_product: {
    id: 'data_product',
    label: 'Data Product',
    plural: 'Data Products',
    icon: Box,
    description: 'A governed bundle of data, dashboards, and APIs — ready to use.',
    accentText: 'text-violet-600',
    accentBg: 'bg-violet-50',
    accentBorder: 'border-violet-200',
  },
  dataset: {
    id: 'dataset',
    label: 'Dataset',
    plural: 'Datasets',
    icon: Database,
    description: 'A curated collection of tables that belong together.',
    accentText: 'text-blue-600',
    accentBg: 'bg-blue-50',
    accentBorder: 'border-blue-200',
  },
  dashboard: {
    id: 'dashboard',
    label: 'Dashboard',
    plural: 'Dashboards',
    icon: LayoutDashboard,
    description:
      'A ready-made set of charts and metrics built on top of data so you can see answers at a glance.',
    accentText: 'text-amber-600',
    accentBg: 'bg-amber-50',
    accentBorder: 'border-amber-200',
  },
  app: {
    id: 'app',
    label: 'App',
    plural: 'Apps',
    icon: Server,
    description: 'An interactive application built on the platform.',
    accentText: 'text-emerald-600',
    accentBg: 'bg-emerald-50',
    accentBorder: 'border-emerald-200',
  },
  genie_space: {
    id: 'genie_space',
    label: 'Genie Space',
    plural: 'Genie Spaces',
    icon: Sparkles,
    description: 'Ask questions of curated data in plain language.',
    accentText: 'text-fuchsia-600',
    accentBg: 'bg-fuchsia-50',
    accentBorder: 'border-fuchsia-200',
  },
  table: {
    id: 'table',
    label: 'Table',
    plural: 'Tables & Views',
    icon: TableIcon,
    description: 'The raw rows and columns — the building block.',
    accentText: 'text-slate-600',
    accentBg: 'bg-slate-100',
    accentBorder: 'border-slate-300',
  },
  job: {
    id: 'job',
    label: 'Job',
    plural: 'Jobs',
    icon: PlaySquare,
    description: 'A scheduled pipeline that produces or refreshes data.',
    accentText: 'text-cyan-600',
    accentBg: 'bg-cyan-50',
    accentBorder: 'border-cyan-200',
  },
};

/** Raw UC table-like types that all map onto the single "table" filter. */
const TABLE_LIKE = new Set(['managed', 'external', 'view', 'table']);

/**
 * Normalize the many raw `type` strings the API returns onto a single
 * `AssetTypeId`. Unknown types fall back to `table` so they never silently
 * disappear from the catalog.
 */
export function normalizeAssetType(raw: string | null | undefined): AssetTypeId {
  const t = String(raw || '').toLowerCase();
  if (t === 'metric_view' || t === 'metric') return 'metric_view';
  if (TABLE_LIKE.has(t)) return 'table';
  if (t === 'data_product') return 'data_product';
  if (t === 'dataset') return 'dataset';
  if (t === 'dashboard') return 'dashboard';
  if (t === 'app') return 'app';
  if (t === 'genie_space') return 'genie_space';
  if (t === 'job') return 'job';
  return 'table';
}

export function getAssetTypeMeta(raw: string | null | undefined): AssetTypeMeta {
  return ASSET_TYPES[normalizeAssetType(raw)];
}
