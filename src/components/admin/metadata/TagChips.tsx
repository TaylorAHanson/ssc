import { Lock, X } from 'lucide-react';
import type { Tags } from '../../../types/metadata';
import { cn } from '../../../lib/utils';

export type ChipTone = 'default' | 'added' | 'removed' | 'changed' | 'exempt' | 'muted';

const CHIP_TONES: Record<ChipTone, string> = {
  default: 'border-gray-200 bg-white text-gray-700',
  added: 'border-green-300 bg-green-50 text-green-800',
  removed: 'border-red-200 bg-red-50 text-red-700 line-through',
  changed: 'border-amber-300 bg-amber-50 text-amber-900',
  exempt: 'border-dashed border-gray-300 bg-gray-50 text-gray-500',
  muted: 'border-gray-200 bg-gray-50 text-gray-500',
};

export function TagChip({ tagKey, value, tone = 'default', title, onRemove, removeLabel }: {
  tagKey: string;
  value: string | null | undefined;
  tone?: ChipTone;
  title?: string;
  onRemove?: () => void;
  removeLabel?: string;
}) {
  const keyOnly = value === null || value === undefined || value === '';
  return (
    <span
      title={title ?? (keyOnly ? `${tagKey} (key only, no value)` : `${tagKey} = ${value}`)}
      className={cn('inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-xs', CHIP_TONES[tone])}
    >
      {tone === 'exempt' && <Lock className="h-3 w-3 shrink-0" aria-label="Managed outside this tool" />}
      <span className="truncate">
        {tagKey}
        {!keyOnly && (
          <>
            <span className="text-gray-400">=</span>
            {value}
          </>
        )}
      </span>
      {onRemove && (
        <button type="button" onClick={onRemove} className="shrink-0 rounded-full hover:bg-black/10" aria-label={removeLabel ?? `Remove ${tagKey}`}>
          <X className="h-3 w-3" />
        </button>
      )}
    </span>
  );
}

/** A compact, wrapping list of tags; exempt keys are marked and sorted last. */
export function TagList({ tags, exemptKeys = [], max, emptyText = 'No tags' }: {
  tags: Tags | null | undefined;
  exemptKeys?: string[];
  max?: number;
  emptyText?: string;
}) {
  const entries = Object.entries(tags ?? {}).sort(
    ([a], [b]) => Number(exemptKeys.includes(a)) - Number(exemptKeys.includes(b)) || a.localeCompare(b),
  );
  if (!entries.length) return <span className="text-xs text-gray-400">{emptyText}</span>;
  const shown = max ? entries.slice(0, max) : entries;
  return (
    <span className="flex flex-wrap gap-1">
      {shown.map(([k, v]) => (
        <TagChip key={k} tagKey={k} value={v} tone={exemptKeys.includes(k) ? 'exempt' : 'default'} />
      ))}
      {shown.length < entries.length && (
        <span className="text-xs text-gray-500" title={entries.slice(shown.length).map(([k]) => k).join(', ')}>
          +{entries.length - shown.length} more
        </span>
      )}
    </span>
  );
}
