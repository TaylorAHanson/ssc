import { useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, Columns3, Layers, Loader2, X } from 'lucide-react';
import { BulkTagBar } from './BulkTagBar';
import { TagChips } from './TagChips';
import { TagRowsEditor } from './TagRowsEditor';
import type { EditTarget, TagRow } from './tagModel';
import { datasetMove, diffTarget, typeLabel } from './tagModel';

export interface ColumnsState {
  status: 'loading' | 'loaded' | 'error';
  /** Column target keys, in table order. */
  keys: string[];
  dataTypes: Record<string, string>;
  error?: string | null;
}

// Show the column filter (and its "apply to all shown" bar) once a table has
// more columns than are comfortable to edit one by one.
const COLUMN_TOOLS_FROM = 6;

function ChangeBadge({ target }: { target: EditTarget }) {
  const d = diffTarget(target);
  if (!d.changed) return null;
  const set = Object.keys(d.set).length;
  return (
    <span className="flex items-center gap-1 shrink-0">
      {set > 0 && (
        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-100 text-emerald-800 font-medium">+{set}</span>
      )}
      {d.unset.length > 0 && (
        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-rose-100 text-rose-800 font-medium">−{d.unset.length}</span>
      )}
    </span>
  );
}

export function TagTargetRow({
  target,
  targets,
  columns,
  columnsEditable,
  expanded,
  onToggle,
  onChangeRows,
  onBulk,
  onRemove,
  onLoadColumns,
}: {
  target: EditTarget;
  targets: Record<string, EditTarget>;
  columns?: ColumnsState;
  columnsEditable: boolean;
  expanded: Set<string>;
  onToggle: (key: string) => void;
  onChangeRows: (key: string, rows: TagRow[]) => void;
  /** Set (value) or remove (null) one tag on several targets at once. */
  onBulk: (keys: string[], key: string, value: string | null) => void;
  onRemove: () => void;
  onLoadColumns: () => void;
}) {
  const [columnFilter, setColumnFilter] = useState('');
  const isOpen = expanded.has(target.key);
  const columnsOpen = expanded.has(`${target.key}::#columns`);
  const changed = diffTarget(target).changed;
  const move = datasetMove(target);
  const moveText = move
    ? move.from && move.to
      ? `Moves this table from dataset '${move.from}' to '${move.to}'.`
      : move.to
      ? `Adds this table to dataset '${move.to}'.`
      : `Takes this table out of dataset '${move.from}'.`
    : null;

  const columnTargets = (columns?.keys ?? []).map((k) => targets[k]).filter(Boolean);
  const changedColumns = columnTargets.filter((c) => diffTarget(c).changed).length;
  const visibleColumns = columnFilter
    ? columnTargets.filter((c) => (c.column ?? '').toLowerCase().includes(columnFilter.toLowerCase()))
    : columnTargets;
  const showColumnTools = columnsEditable && columnTargets.length >= COLUMN_TOOLS_FROM;

  const toggleColumns = () => {
    if (!columns) onLoadColumns();
    onToggle(`${target.key}::#columns`);
  };

  return (
    <div className={`rounded-lg border ${changed || changedColumns ? 'border-blue-300 bg-blue-50/20' : 'border-gray-200 bg-white'}`}>
      {/* Summary line */}
      <div className="flex items-center gap-2.5 px-3 py-2 cursor-pointer" onClick={() => onToggle(target.key)}>
        {isOpen ? (
          <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 text-gray-400 shrink-0" />
        )}
        <code className="text-xs font-semibold text-gray-900 truncate">{target.table}</code>
        <span className="text-[10px] uppercase text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded shrink-0">
          {typeLabel(target.objectType)}
        </span>
        {!target.exists && (
          <span className="flex items-center gap-1 text-[11px] text-amber-700 shrink-0" title="Not found in Unity Catalog">
            <AlertTriangle className="w-3 h-3" /> not found
          </span>
        )}
        <span className="min-w-0 flex-1 overflow-hidden">{!isOpen && <TagChips target={target} max={5} />}</span>
        {move && (
          <span
            className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-800 font-medium shrink-0"
            title={moveText ?? undefined}
          >
            <Layers className="w-3 h-3" />
            {move.to ? `→ ${move.to}` : 'leaves dataset'}
          </span>
        )}
        {changedColumns > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-100 text-blue-800 font-medium shrink-0">
            {changedColumns} column{changedColumns === 1 ? '' : 's'}
          </span>
        )}
        <ChangeBadge target={target} />
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          title="Remove from this list (discards its edits)"
          className="p-1 rounded hover:bg-gray-100 shrink-0"
        >
          <X className="w-3.5 h-3.5 text-gray-400" />
        </button>
      </div>

      {isOpen && (
        <div className="border-t border-gray-100 px-3 pb-3 pt-2.5 pl-9 space-y-3">
          <TagRowsEditor rows={target.rows} onChange={(rows) => onChangeRows(target.key, rows)} />
          {moveText && (
            <p className="flex items-start gap-1.5 max-w-3xl rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-900">
              <Layers className="w-3.5 h-3.5 mt-px shrink-0 text-amber-600" />
              <span>
                {moveText} Dataset membership decides which data product, contract and certification this table
                belongs to.
              </span>
            </p>
          )}

          {/* Columns */}
          <div className="border-t border-dashed border-gray-200 pt-2.5">
            <button
              type="button"
              onClick={toggleColumns}
              className="flex items-center gap-1.5 text-xs font-medium text-gray-700 hover:text-gray-900"
            >
              {columnsOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              <Columns3 className="w-3.5 h-3.5 text-gray-500" />
              {columnsOpen ? 'Columns' : 'Show columns'}
              {columns?.status === 'loaded' && <span className="text-gray-400 font-normal">({columns.keys.length})</span>}
            </button>

            {columnsOpen && (
              <div className="mt-2 space-y-1.5">
                {columns?.status === 'loading' && (
                  <p className="flex items-center gap-2 text-xs text-gray-500">
                    <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-600" /> Loading columns…
                  </p>
                )}
                {columns?.status === 'error' && <p className="text-xs text-rose-700">{columns.error}</p>}
                {columns?.status === 'loaded' && columnTargets.length === 0 && (
                  <p className="text-xs text-gray-400 italic">No columns found.</p>
                )}
                {!columnsEditable && columnTargets.length > 0 && (
                  <p className="text-[11px] text-gray-500">
                    Column tags are read-only here because changes go through GitOps pull requests, which cover
                    table and view tags only. Switch to Local Execution Mode to edit them.
                  </p>
                )}
                {showColumnTools && (
                  <div className="space-y-2 rounded-md border border-gray-200 bg-gray-50 px-2.5 py-2">
                    <input
                      value={columnFilter}
                      onChange={(e) => setColumnFilter(e.target.value)}
                      placeholder="Filter columns by name…"
                      aria-label="Filter columns"
                      className="w-full max-w-xs border border-gray-300 rounded-md h-7 px-2.5 text-xs bg-white focus:ring-1 focus:ring-blue-500"
                    />
                    <BulkTagBar
                      count={visibleColumns.length}
                      noun={visibleColumns.length === 1 ? 'column' : 'columns'}
                      onApply={(k, v) => onBulk(visibleColumns.map((c) => c.key), k, v)}
                      onRemove={(k) => onBulk(visibleColumns.map((c) => c.key), k, null)}
                    />
                  </div>
                )}
                {visibleColumns.map((col) => {
                  const colOpen = expanded.has(col.key);
                  return (
                    <div key={col.key} className="rounded-md border border-gray-100 bg-white">
                      <div
                        className={`flex items-center gap-2.5 px-2.5 py-1.5 ${columnsEditable ? 'cursor-pointer' : ''}`}
                        onClick={() => columnsEditable && onToggle(col.key)}
                      >
                        {columnsEditable &&
                          (colOpen ? (
                            <ChevronDown className="w-3 h-3 text-gray-400 shrink-0" />
                          ) : (
                            <ChevronRight className="w-3 h-3 text-gray-400 shrink-0" />
                          ))}
                        <code className="text-xs text-gray-900">{col.column}</code>
                        <span className="text-[10px] text-gray-400 font-mono shrink-0">{columns?.dataTypes[col.key]}</span>
                        <span className="min-w-0 flex-1 overflow-hidden">{!colOpen && <TagChips target={col} max={4} />}</span>
                        <ChangeBadge target={col} />
                      </div>
                      {colOpen && (
                        <div className="border-t border-gray-100 px-2.5 py-2 pl-8">
                          <TagRowsEditor rows={col.rows} onChange={(rows) => onChangeRows(col.key, rows)} />
                        </div>
                      )}
                    </div>
                  );
                })}
                {columnFilter && visibleColumns.length === 0 && (
                  <p className="text-xs text-gray-400 italic">No columns match “{columnFilter}”.</p>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
