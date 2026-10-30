import { useId, type KeyboardEvent } from 'react';
import { ChevronDown, ChevronUp, Replace, Search, X } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import type { FindOptions } from './findReplace';
import { plural } from './utils';

const SMALL = 'h-7 px-2.5 text-xs';

interface FindReplacePanelProps {
  options: FindOptions;
  onOptionsChange: (next: FindOptions) => void;
  /** Column index, or null for every replaceable column. */
  scope: number | null;
  onScopeChange: (scope: number | null) => void;
  columns: { col: number; header: string }[];
  /** The sheet has imported-only columns, which find and replace leaves alone. */
  hasReadOnly: boolean;
  matchCount: number;
  rowCount: number;
  /** 0-based position of the selected match, or -1 when none is selected. */
  current: number;
  /** Cells whose value would actually change; a match whose replacement is identical isn't counted. */
  changeCount: number;
  /** Shown under the controls, e.g. what the last Replace all did or that it would pass the save limit. */
  note: string | null;
  onStep: (direction: 1 | -1) => void;
  onReplaceOne: () => void;
  onReplaceAll: () => void;
  onClose: () => void;
}

/** Find text in the rows shown and stage replacements; nothing is saved until the staged changes are committed. */
export function FindReplacePanel({
  options,
  onOptionsChange,
  scope,
  onScopeChange,
  columns,
  hasReadOnly,
  matchCount,
  rowCount,
  current,
  changeCount,
  note,
  onStep,
  onReplaceOne,
  onReplaceAll,
  onClose,
}: FindReplacePanelProps) {
  const ids = useId();
  const set = (patch: Partial<FindOptions>) => onOptionsChange({ ...options, ...patch });

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onClose();
    }
  };
  const onFindKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    onStep(e.shiftKey ? -1 : 1);
  };
  const onReplaceKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    onReplaceOne();
  };

  const status = !options.find.trim()
    ? 'Type the text to find'
    : matchCount === 0
      ? 'No matches in the rows shown'
      : current >= 0
        ? `${(current + 1).toLocaleString()} of ${matchCount.toLocaleString()}`
        : `${plural(matchCount, 'cell')} in ${plural(rowCount, 'row')}`;

  return (
    <div
      role="search"
      aria-label="Find and replace"
      onKeyDown={onKeyDown}
      className="space-y-2 rounded-lg border border-gray-200 bg-gray-50 p-2.5"
    >
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[180px] flex-1">
          <Search className="absolute left-2.5 top-2 h-4 w-4 text-gray-400" aria-hidden="true" />
          <Input
            autoFocus
            value={options.find}
            onChange={(e) => set({ find: e.target.value })}
            onKeyDown={onFindKey}
            placeholder="Find"
            aria-label="Find"
            aria-describedby={`${ids}-status`}
            className="h-8 bg-white pl-8 text-[13px]"
          />
        </div>
        <div className="relative min-w-[180px] flex-1">
          <Replace className="absolute left-2.5 top-2 h-4 w-4 text-gray-400" aria-hidden="true" />
          <Input
            value={options.replace}
            onChange={(e) => set({ replace: e.target.value })}
            onKeyDown={onReplaceKey}
            placeholder="Replace with (blank removes it)"
            aria-label="Replace with"
            className="h-8 bg-white pl-8 text-[13px]"
          />
        </div>
        <select
          value={scope === null ? 'all' : String(scope)}
          onChange={(e) => onScopeChange(e.target.value === 'all' ? null : Number(e.target.value))}
          aria-label="Look in"
          className="h-8 rounded-md border border-gray-300 bg-white px-2 text-[13px]"
        >
          <option value="all">All columns</option>
          {columns.map((c) => (
            <option key={c.col} value={c.col}>{c.header}</option>
          ))}
        </select>
        <label className="inline-flex items-center gap-1.5 text-xs text-gray-700">
          <input type="checkbox" checked={options.matchCase} onChange={(e) => set({ matchCase: e.target.checked })} />
          Match case
        </label>
        <label
          className="inline-flex items-center gap-1.5 text-xs text-gray-700"
          title="Match a whole cell, or one whole value in a cell holding several separated by “;”, instead of text inside it"
        >
          <input type="checkbox" checked={options.wholeValue} onChange={(e) => set({ wholeValue: e.target.checked })} />
          Whole value
        </label>
        <button type="button" onClick={onClose} aria-label="Close find and replace" className="ml-auto text-gray-400 hover:text-gray-700">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span id={`${ids}-status`} role="status" aria-live="polite" className="text-xs text-gray-600">
          {status}
        </span>
        <span className="ml-auto inline-flex flex-wrap gap-1.5">
          <Button size="sm" variant="outline" className={SMALL} disabled={matchCount === 0} onClick={() => onStep(-1)} aria-label="Previous match">
            <ChevronUp className="h-3.5 w-3.5" />
          </Button>
          <Button size="sm" variant="outline" className={SMALL} disabled={matchCount === 0} onClick={() => onStep(1)} aria-label="Next match">
            <ChevronDown className="h-3.5 w-3.5" />
          </Button>
          <Button size="sm" variant="outline" className={SMALL} disabled={matchCount === 0} onClick={onReplaceOne}>
            Replace
          </Button>
          <Button size="sm" className={SMALL} disabled={changeCount === 0} onClick={onReplaceAll}>
            Replace all{changeCount > 0 ? ` (${changeCount.toLocaleString()})` : ''}
          </Button>
        </span>
      </div>
      <p className="text-xs text-gray-500">
        Looks only in the rows shown, across every page; search, filters and the row view narrow it.
        {hasReadOnly ? ' Read-only columns are skipped.' : ''} Replacements are staged for review, like any edit.
      </p>
      {note && <p className="text-xs text-gray-800">{note}</p>}
    </div>
  );
}
