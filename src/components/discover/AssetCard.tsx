import { ShieldCheck, ExternalLink, ChevronRight, User } from 'lucide-react';
import type { DataAsset } from '../../services/api';
import { assetWorkspaceUrl } from '../../lib/databricksLinks';
import { AssetTypeBadge } from '../../lib/assetTypes';

interface AssetCardProps {
  asset: DataAsset;
  workspaceUrl: string;
  isSelected?: boolean;
  onSelect: (asset: DataAsset) => void;
  /** Optional line above the title, e.g. "Finance › Planning & Budgeting" in search results. */
  context?: string;
}

/** Card for a table or view — the same shape as MetricViewCard so both sections read alike. */
export function AssetCard({ asset, workspaceUrl, isSelected = false, onSelect, context }: AssetCardProps) {
  const href = assetWorkspaceUrl(workspaceUrl, asset);

  return (
    <div
      onClick={() => onSelect(asset)}
      className={`group relative rounded-2xl p-5 transition-all cursor-pointer flex flex-col justify-between ${
        isSelected
          ? 'bg-indigo-50 border-2 border-indigo-600 shadow-md'
          : 'bg-white border border-slate-200 shadow-xs hover:shadow-md hover:border-indigo-300'
      }`}
    >
      <div>
        {href && (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="absolute top-4 right-4 text-slate-400 hover:text-slate-600 p-1 rounded hover:bg-slate-100 transition-colors"
            title="Open in Catalog Explorer"
          >
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        )}

        {asset.certified && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 mb-2 rounded-full text-xs font-medium bg-emerald-100 text-emerald-800">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
            Certified
          </span>
        )}

        {context && <div className="text-[11px] font-semibold text-slate-500 mb-1 pr-6 truncate">{context}</div>}
        <h4 className="text-base font-bold text-slate-900 group-hover:text-indigo-700 transition-colors mb-1 pr-6 break-all">
          {asset.table_name}
        </h4>
        <div className="text-[11px] font-mono text-slate-400 mb-2 truncate">
          {asset.catalog}.{asset.schema_name}
        </div>

        {asset.description ? (
          <p className="text-xs text-slate-600 line-clamp-3 mb-4 leading-relaxed">{asset.description}</p>
        ) : (
          <p className="mb-4 text-[11px] text-slate-400 italic">No description.</p>
        )}
      </div>

      <div className="pt-3 border-t border-slate-100 flex items-center justify-between gap-2 text-xs text-slate-500">
        <div className="flex items-center gap-2 min-w-0">
          <AssetTypeBadge type={asset.type} />
          {asset.owner && (
            <span className="inline-flex items-center gap-1 truncate" title={asset.owner}>
              <User className="w-3 h-3 text-slate-400 shrink-0" />
              <span className="truncate">{asset.owner}</span>
            </span>
          )}
        </div>
        <div className="inline-flex items-center gap-1 text-indigo-700 font-semibold shrink-0 group-hover:translate-x-0.5 transition-transform">
          <span>Details</span>
          <ChevronRight className="w-3.5 h-3.5" />
        </div>
      </div>
    </div>
  );
}
