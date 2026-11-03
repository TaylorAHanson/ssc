/**
 * UI pieces for the shared asset taxonomy (badge, card, nesting explainer).
 * The taxonomy data and helpers live in `assetTypeMeta.ts`.
 */
import {
  Box,
  Database,
  Table as TableIcon,
  LayoutDashboard,
  ShieldCheck,
  Pin,
  ChevronDown,
  ChevronRight,
  Webhook,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Fragment, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { ASSET_TYPES, getAssetTypeMeta } from './assetTypeMeta';

/**
 * Small inline badge for an asset's type — icon + label in the type's accent
 * colors. Used in tables, cards and modals so the type is recognizable at a
 * glance everywhere.
 */
export function AssetTypeBadge({
  type,
  className = '',
}: {
  type: string | null | undefined;
  className?: string;
}) {
  const meta = getAssetTypeMeta(type);
  const Icon = meta.icon;
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border ${meta.accentBg} ${meta.accentText} ${meta.accentBorder} ${className}`}
    >
      <Icon className="w-3.5 h-3.5" />
      {meta.label}
    </span>
  );
}

/**
 * Compact, icon-forward hint showing how the catalog nests
 * (Data Product ▸ Dataset ▸ Table). Collapsed by default — the inline
 * colored chips give the instant "aha" while the wordier descriptions stay
 * tucked behind a toggle.
 */
export function AssetTaxonomyExplainer() {
  const [open, setOpen] = useState(false);
  const levels = [ASSET_TYPES.data_product, ASSET_TYPES.dataset, ASSET_TYPES.table];

  return (
    <section className="rounded-xl border border-gray-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex items-center justify-between gap-3 px-4 py-2.5 text-left rounded-xl hover:bg-gray-50 transition-colors"
      >
        <div className="flex items-center gap-x-3 gap-y-1.5 flex-wrap min-w-0">
          <span className="text-xs font-semibold uppercase tracking-wide text-gray-500 shrink-0">
            How it nests
          </span>
          <div className="flex items-center gap-1.5">
            {levels.map((m, i) => (
              <Fragment key={m.id}>
                <span
                  className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-semibold border ${m.accentBg} ${m.accentText} ${m.accentBorder}`}
                >
                  <m.icon className="w-3.5 h-3.5" />
                  {m.label}
                </span>
                {i < levels.length - 1 && <ChevronRight className="w-4 h-4 text-gray-300 shrink-0" />}
              </Fragment>
            ))}
          </div>
        </div>
        <span className="flex items-center gap-1 shrink-0 text-xs font-medium text-primary">
          {open ? 'Show less' : 'Show more'}
          <ChevronDown
            className={`w-4 h-4 transition-transform ${open ? 'rotate-180' : ''}`}
          />
        </span>
      </button>

      {open && (
        <div className="px-4 pt-3 pb-4 border-t border-gray-100">
          <TaxonomyNestDiagram />
        </div>
      )}
    </section>
  );
}

/**
 * "Box in a box" visualization of how the catalog nests. Reads outside-in:
 * a Data Product wraps one or more Datasets (each a stack of Tables/Views),
 * plus Dashboards and APIs built on top. Intentionally icon-forward — the
 * shapes and colors carry the meaning, labels are just anchors.
 */
function TaxonomyNestDiagram() {
  return (
    <div className="inline-block w-fit max-w-full rounded-2xl border-2 border-violet-200 bg-violet-50/40 p-4">
      {/* Outer container: Data Product */}
      <NestHeader icon={Box} label="Data Product" tone="text-violet-700" chip="bg-violet-100 text-violet-600" />

      {/* Datasets sit inside the product, each holding multiple tables/views.
          Sized to content (flex) so the diagram never stretches full width. */}
      <div className="flex flex-wrap gap-3 mt-3">
        <DatasetBox tableCount={3} />
        <DatasetBox tableCount={2} />
      </div>

      {/* ...alongside the dashboards and APIs the product also exposes. */}
      <div className="flex flex-wrap items-center gap-2 mt-3">
        <LeafChip icon={LayoutDashboard} label="Dashboard" className="bg-amber-50 text-amber-700 border-amber-200" />
        <LeafChip icon={LayoutDashboard} label="Dashboard" className="bg-amber-50 text-amber-700 border-amber-200" />
        <LeafChip icon={Webhook} label="API" className="bg-emerald-50 text-emerald-700 border-emerald-200" />
        <LeafChip icon={Webhook} label="API" className="bg-emerald-50 text-emerald-700 border-emerald-200" />
      </div>
    </div>
  );
}

function NestHeader({
  icon: Icon,
  label,
  tone,
  chip,
}: {
  icon: LucideIcon;
  label: string;
  tone: string;
  chip: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className={`w-7 h-7 rounded-lg flex items-center justify-center ${chip}`}>
        <Icon className="w-4 h-4" />
      </span>
      <span className={`text-sm font-bold ${tone}`}>{label}</span>
    </div>
  );
}

function DatasetBox({ tableCount }: { tableCount: number }) {
  return (
    <div className="rounded-xl border-2 border-blue-200 bg-white/70 p-3">
      <NestHeader icon={Database} label="Dataset" tone="text-blue-700" chip="bg-blue-50 text-blue-600" />
      <div className="flex flex-wrap gap-1.5 mt-2.5">
        {Array.from({ length: tableCount }).map((_, i) => (
          <span
            key={i}
            title="Table / View"
            className="w-8 h-8 rounded-md bg-slate-100 border border-slate-300 text-slate-500 flex items-center justify-center"
          >
            <TableIcon className="w-4 h-4" />
          </span>
        ))}
      </div>
    </div>
  );
}

function LeafChip({
  icon: Icon,
  label,
  className,
}: {
  icon: LucideIcon;
  label: string;
  className: string;
}) {
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold border ${className}`}>
      <Icon className="w-4 h-4" />
      {label}
    </span>
  );
}

/**
 * Compact tile for a single asset. Mirrors the Databricks One "For you" card
 * style — a colored type chip, a title, a small subtitle, and a "View details"
 * affordance. `onViewDetails` opens the asset's detail view; the card never
 * issues an agent/LLM query on its own.
 */
export function AssetCard({
  title,
  subtitle,
  type,
  certified,
  onViewDetails,
  pinned,
  onTogglePin,
}: {
  title: string;
  subtitle?: string | null;
  type: string | null | undefined;
  certified?: boolean;
  onViewDetails?: () => void;
  /** When defined, a pin toggle renders in the card's top-right corner. */
  pinned?: boolean;
  onTogglePin?: () => void;
}) {
  const meta = getAssetTypeMeta(type);
  const Icon = meta.icon;
  // Root is a div (not a button) so the pin control can be a real nested
  // button without producing invalid <button>-in-<button> markup.
  const handleKey = (e: ReactKeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onViewDetails?.();
    }
  };
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onViewDetails}
      onKeyDown={handleKey}
      className="group relative text-left bg-white rounded-xl border border-gray-200 p-4 hover:border-primary/40 hover:shadow-md transition-all flex flex-col gap-3 min-w-0 cursor-pointer focus:outline-none focus:ring-2 focus:ring-primary/40"
    >
      <div className="flex items-center justify-between">
        <span className={`w-9 h-9 rounded-lg flex items-center justify-center ${meta.accentBg} ${meta.accentText}`}>
          <Icon className="w-5 h-5" />
        </span>
        <div className="flex items-center gap-1">
          <span className="text-[10px] uppercase tracking-wide font-semibold text-gray-400">
            {meta.label}
          </span>
          {onTogglePin && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onTogglePin();
              }}
              aria-pressed={!!pinned}
              aria-label={pinned ? 'Unpin item' : 'Pin item'}
              title={pinned ? 'Unpin' : 'Pin to Your Pinned Items'}
              className={`p-1 rounded-md transition-colors ${
                pinned
                  ? 'text-primary'
                  : 'text-gray-300 hover:text-gray-500 hover:bg-gray-100'
              }`}
            >
              <Pin className={`w-4 h-4 ${pinned ? 'fill-current' : ''}`} />
            </button>
          )}
        </div>
      </div>
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="font-semibold text-gray-900 truncate group-hover:text-primary transition-colors" title={title}>
            {title}
          </span>
          {certified && <ShieldCheck className="w-3.5 h-3.5 text-green-600 shrink-0" />}
        </div>
        {subtitle && (
          <div className="text-xs text-gray-500 line-clamp-2 mt-0.5 leading-relaxed">{subtitle}</div>
        )}
      </div>
      <div className="mt-auto pt-1 flex items-center text-xs font-medium text-gray-400 group-hover:text-primary transition-colors">
        View details
        <ChevronRight className="w-3.5 h-3.5 ml-0.5 group-hover:translate-x-0.5 transition-transform" />
      </div>
    </div>
  );
}