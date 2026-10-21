import { Link } from 'react-router-dom';
import type { ObjectSummary } from '../../../types/metadata';
import { AttentionBadge } from './badges';
import { useMetadata } from './context';
import { TYPE_LABELS, objectLabel } from './format';
import { browsePath, objectPath } from './paths';
import { effectiveComment, effectiveTags, hasEdits, seedFromSummary, useStagingStore } from './staging';
import { TagList } from './TagChips';

function linkFor(s: ObjectSummary): string {
  if (s.securable_type === 'catalog') return browsePath(s.catalog);
  if (s.securable_type === 'schema') return browsePath(s.catalog, s.schema_name);
  return objectPath(s.name, s.column);
}

/** Rows of name, description, tags and (only when needed) a status; names open the next level. */
export function ObjectList({ items, showPath, counts, countLabel, selected, onToggle, onToggleAll }: {
  items: ObjectSummary[];
  /** Search results: show the full name and the type. */
  showPath?: boolean;
  counts?: Record<string, number>;
  countLabel?: string;
  selected?: Record<string, ObjectSummary>;
  onToggle?: (s: ObjectSummary) => void;
  onToggleAll?: (on: boolean) => void;
}) {
  const selectable = !!selected && !!onToggle;
  const allOn = selectable && items.length > 0 && items.every((s) => selected[s.fqn_key]);
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
      <table className="w-full text-left text-sm">
        <thead className="bg-gray-50 text-xs text-gray-600">
          <tr>
            {selectable && (
              <th scope="col" className="w-8 px-3 py-2">
                <input
                  type="checkbox"
                  checked={allOn}
                  onChange={(e) => onToggleAll?.(e.target.checked)}
                  aria-label="Select everything on this page"
                  className="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
                />
              </th>
            )}
            <th scope="col" className="px-3 py-2 font-medium">Name</th>
            <th scope="col" className="px-3 py-2 font-medium">Tags</th>
            {counts && <th scope="col" className="px-3 py-2 text-right font-medium">{countLabel}</th>}
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <Row key={s.fqn_key} s={s} showPath={showPath} count={counts?.[(s.securable_type === 'catalog' ? s.catalog : s.schema_name ?? '').toLowerCase()]} hasCounts={!!counts} selected={selectable ? !!selected[s.fqn_key] : undefined} onToggle={onToggle} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Row({ s, showPath, count, hasCounts, selected, onToggle }: {
  s: ObjectSummary;
  showPath?: boolean;
  count?: number;
  hasCounts: boolean;
  selected?: boolean;
  onToggle?: (s: ObjectSummary) => void;
}) {
  const { config } = useMetadata();
  const staged = useStagingStore((st) => st.targets[s.fqn_key]);
  const seed = seedFromSummary(s);
  const tags = staged ? effectiveTags(staged) : seed.baseTags;
  const comment = staged ? effectiveComment(staged) : seed.baseComment;
  const label = showPath ? objectLabel(s.name, s.column) : s.column ?? s.object_name ?? s.schema_name ?? s.catalog;
  return (
    <tr className={selected ? 'border-t border-gray-100 bg-primary/5 align-top' : 'border-t border-gray-100 align-top hover:bg-gray-50'}>
      {selected !== undefined && (
        <td className="px-3 py-2.5">
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggle?.(s)}
            aria-label={`Select ${objectLabel(s.name, s.column)}`}
            className="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
          />
        </td>
      )}
      <td className="max-w-xl px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <Link to={linkFor(s)} className="break-all font-mono text-sm font-medium text-gray-900 hover:text-primary hover:underline">
            {label}
          </Link>
          {showPath && <span className="text-xs text-gray-500">{TYPE_LABELS[s.securable_type]}</span>}
          <AttentionBadge summary={s} unsaved={!!staged && hasEdits(staged)} />
        </div>
        {comment && <p className="mt-0.5 line-clamp-2 text-xs text-gray-600">{comment}</p>}
      </td>
      <td className="px-3 py-2.5">
        <TagList tags={tags} exemptKeys={config.exempt_keys} max={4} emptyText="" />
      </td>
      {hasCounts && <td className="px-3 py-2.5 text-right text-xs tabular-nums text-gray-500">{count?.toLocaleString() ?? '—'}</td>}
    </tr>
  );
}
