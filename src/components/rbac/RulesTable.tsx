import { useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  columnFilteringFeature,
  createColumnHelper,
  createFilteredRowModel,
  createSortedRowModel,
  filterFn_includesString,
  globalFilteringFeature,
  rowSortingFeature,
  sortFn_alphanumeric,
  tableFeatures,
  useTable,
  type ColumnFiltersState,
  type SortingState,
} from '@tanstack/react-table';
import { ArrowDown, ArrowUp, ArrowUpDown, Clock, Copy, Lock, Plus, Search, Trash2, Undo2, X } from 'lucide-react';
import type { RbacField, RbacSheetInfo } from '../../types/rbac';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { cn } from '../../lib/utils';
import type { GridRow } from './staging';
import { Badge } from './ui';
import { PACKED_FIELDS, cellValue, splitPacked } from './utils';

const STICKY_END = 'sticky right-0 border-b border-l border-gray-200 px-2';

const PAGE_SIZE = 100;

const features = tableFeatures({
  columnFilteringFeature,
  globalFilteringFeature,
  rowSortingFeature,
  filteredRowModel: createFilteredRowModel(),
  sortedRowModel: createSortedRowModel(),
  filterFns: { includesString: filterFn_includesString },
  sortFns: { alphanumeric: sortFn_alphanumeric },
});

const helper = createColumnHelper<typeof features, GridRow>();

type View = 'all' | 'staged' | 'pending';

interface RulesTableProps {
  sheet: RbacSheetInfo;
  rows: GridRow[];
  editable: boolean;
  onEdit: (row: GridRow, field: RbacField, value: string) => void;
  onDelete: (row: GridRow) => void;
  onUndo: (row: GridRow) => void;
  /** Stage a new row (a copy of `from` when given) and return its key. */
  onAdd: (from?: GridRow) => string;
}

const cellId = (rowKey: string, col: number) => `rbac-cell-${rowKey}-${col}`;

function focusCell(rowKey: string, col: number) {
  window.requestAnimationFrame(() => document.getElementById(cellId(rowKey, col))?.focus());
}

/** The sheet's rows under its own headers: search, column filters, sorting, paging and inline edits. */
export function RulesTable({ sheet, rows, editable, onEdit, onDelete, onUndo, onAdd }: RulesTableProps) {
  const [globalFilter, setGlobalFilter] = useState('');
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [view, setView] = useState<View>('all');
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState<{ rowKey: string; col: number } | null>(null);

  const data = useMemo(
    () =>
      view === 'staged'
        ? rows.filter((r) => r.state !== 'clean')
        : view === 'pending'
          ? rows.filter((r) => r.pendingRequestIds.length > 0)
          : rows,
    [rows, view],
  );

  const columns = useMemo(
    () =>
      helper.columns(
        sheet.columns.map((col, i) =>
          helper.accessor((row: GridRow) => cellValue(row, col, i), {
            id: `c${i}`,
            header: col.header,
            sortFn: 'alphanumeric',
            filterFn: 'includesString',
          }),
        ),
      ),
    [sheet.columns],
  );

  const table = useTable({
    features,
    columns,
    data,
    getRowId: (row) => row.key,
    state: { globalFilter, columnFilters, sorting },
    onGlobalFilterChange: setGlobalFilter,
    onColumnFiltersChange: (updater) => {
      setColumnFilters(updater);
      setPage(0);
    },
    onSortingChange: setSorting,
    globalFilterFn: 'includesString',
    getColumnCanGlobalFilter: () => true,
  });

  const visible = table.getRowModel().rows;
  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const pageIndex = Math.min(page, pageCount - 1);
  const pageRows = visible.slice(pageIndex * PAGE_SIZE, (pageIndex + 1) * PAGE_SIZE);
  const firstEditable = sheet.columns.findIndex((c) => c.field);
  const filtered = !!globalFilter || columnFilters.length > 0 || view !== 'all';

  const clearFilters = () => {
    setGlobalFilter('');
    setColumnFilters([]);
    setView('all');
    setPage(0);
  };

  const add = (from?: GridRow) => {
    const key = onAdd(from);
    clearFilters();
    setSorting([]);
    // New rows are appended, so with filters and sorting cleared it lands on the last page.
    setPage(Math.floor(rows.length / PAGE_SIZE));
    if (firstEditable >= 0) setEditing({ rowKey: key, col: firstEditable });
  };

  const finishEdit = (row: GridRow, col: number, value: string | null, refocus: boolean) => {
    const field = sheet.columns[col]?.field;
    if (value !== null && field) onEdit(row, field, value);
    setEditing(null);
    if (refocus) focusCell(row.key, col);
  };

  const counts = useMemo(
    () => ({
      staged: rows.filter((r) => r.state !== 'clean').length,
      pending: rows.filter((r) => r.pendingRequestIds.length > 0).length,
    }),
    [rows],
  );

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[240px] flex-1">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-gray-400" />
          <Input
            value={globalFilter}
            onChange={(e) => {
              setGlobalFilter(e.target.value);
              setPage(0);
            }}
            placeholder="Search every column"
            aria-label="Search every column"
            className="h-9 pl-8"
          />
        </div>
        <select
          value={view}
          onChange={(e) => {
            setView(e.target.value as View);
            setPage(0);
          }}
          aria-label="Show rows"
          className="h-9 rounded-md border border-gray-300 bg-white px-2 text-sm"
        >
          <option value="all">All rows</option>
          <option value="staged">Staged changes ({counts.staged})</option>
          <option value="pending">Pending requests ({counts.pending})</option>
        </select>
        <span className="text-xs text-gray-500">
          {filtered ? `${visible.length.toLocaleString()} of ${rows.length.toLocaleString()} rows` : `${rows.length.toLocaleString()} rows`}
        </span>
        {filtered && (
          <button type="button" onClick={clearFilters} className="text-xs text-primary hover:underline">
            Clear filters
          </button>
        )}
        {editable && (
          <Button size="sm" onClick={() => add()} className="ml-auto gap-1.5">
            <Plus className="h-4 w-4" /> Add row
          </Button>
        )}
      </div>

      <div className="max-h-[65vh] overflow-auto rounded-lg border border-gray-200 bg-white">
        <table className="w-full border-separate border-spacing-0 text-sm">
          <thead className="sticky top-0 z-10 bg-gray-50 text-left text-xs text-gray-600">
            <tr>
              <th scope="col" className="border-b border-gray-200 px-2 py-2 font-medium">#</th>
              <th scope="col" className="border-b border-gray-200 px-2 py-2 font-medium">Status</th>
              {table.getHeaderGroups()[0]?.headers.map((header) => {
                const index = Number(header.column.id.slice(1));
                const col = sheet.columns[index];
                const sorted = header.column.getIsSorted();
                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : 'none'}
                    className="whitespace-nowrap border-b border-gray-200 px-2 py-2 font-medium"
                  >
                    <button
                      type="button"
                      onClick={header.column.getToggleSortingHandler()}
                      className="inline-flex items-center gap-1 hover:text-gray-900"
                      title="Sort"
                    >
                      {col?.header}
                      {sorted === 'asc' ? <ArrowUp className="h-3 w-3" /> : sorted === 'desc' ? <ArrowDown className="h-3 w-3" /> : <ArrowUpDown className="h-3 w-3 text-gray-300" />}
                    </button>
                    {col && !col.field && (
                      <span title="Not a modelled column: shown and exported exactly as imported, not editable here.">
                        <Lock className="ml-1 inline h-3 w-3 text-gray-400" aria-label="Read-only column" />
                      </span>
                    )}
                  </th>
                );
              })}
              {editable && <th scope="col" className={cn(STICKY_END, 'bg-gray-50 py-2 text-right font-medium')}>Actions</th>}
            </tr>
            <tr>
              <th className="border-b border-gray-200 px-2 pb-2" />
              <th className="border-b border-gray-200 px-2 pb-2" />
              {table.getHeaderGroups()[0]?.headers.map((header) => (
                <th key={header.id} className="border-b border-gray-200 px-2 pb-2 font-normal">
                  <Input
                    value={(header.column.getFilterValue() as string | undefined) ?? ''}
                    onChange={(e) => header.column.setFilterValue(e.target.value || undefined)}
                    placeholder="Filter"
                    aria-label={`Filter ${sheet.columns[Number(header.column.id.slice(1))]?.header ?? ''}`}
                    className="h-7 min-w-[6rem] px-2 text-xs"
                  />
                </th>
              ))}
              {editable && <th className={cn(STICKY_END, 'bg-gray-50 pb-2')} />}
            </tr>
          </thead>
          <tbody>
            {pageRows.length === 0 && (
              <tr>
                <td colSpan={sheet.columns.length + (editable ? 3 : 2)} className="px-3 py-8 text-center text-sm text-gray-500">
                  {rows.length === 0 ? (
                    editable ? 'This sheet has no rows yet. Use Add row to start one.' : 'This sheet has no rows yet.'
                  ) : (
                    <>
                      No rows match.{' '}
                      <button type="button" onClick={clearFilters} className="text-primary hover:underline">Clear filters</button>
                    </>
                  )}
                </td>
              </tr>
            )}
            {pageRows.map((tableRow) => {
              const row = tableRow.original;
              const deleted = row.state === 'deleted';
              return (
                <tr
                  key={row.key}
                  className={cn(
                    'align-top',
                    row.state === 'new' && 'bg-green-50/70',
                    deleted && 'bg-red-50/40 text-gray-400',
                  )}
                >
                  <td className="border-b border-gray-100 px-2 py-1.5 text-xs tabular-nums text-gray-400">{row.number}</td>
                  <td className="border-b border-gray-100 px-2 py-1.5">
                    <RowStatus row={row} />
                  </td>
                  {sheet.columns.map((col, i) => {
                    const value = cellValue(row, col, i);
                    const modified = !!col.field && row.changed.has(col.field);
                    const was = modified && row.original ? cellValue(row.original, col, i) : null;
                    const isEditing = editing?.rowKey === row.key && editing.col === i;
                    const canEdit = editable && !!col.field && !deleted;
                    return (
                      <td
                        key={i}
                        className={cn(
                          'border-b border-gray-100 px-2 py-1.5',
                          modified && 'bg-amber-50 shadow-[inset_0_0_0_1px_rgb(252,211,77)]',
                          !col.field && 'text-gray-500',
                          deleted && 'line-through',
                        )}
                        title={was !== null ? `Was: ${was || '(empty)'}` : undefined}
                      >
                        {isEditing ? (
                          <CellEditor
                            initial={value}
                            label={`${col.header}, row ${row.number}`}
                            packed={PACKED_FIELDS.has(col.field ?? '')}
                            onDone={(next, refocus) => finishEdit(row, i, next, refocus)}
                          />
                        ) : canEdit ? (
                          <button
                            id={cellId(row.key, i)}
                            type="button"
                            onClick={() => setEditing({ rowKey: row.key, col: i })}
                            aria-label={`Edit ${col.header}, row ${row.number}: ${value || 'empty'}`}
                            className="-mx-1 block min-h-[1.5rem] w-full rounded px-1 text-left hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                          >
                            <CellContent value={value} packed={PACKED_FIELDS.has(col.field ?? '')} />
                          </button>
                        ) : (
                          <CellContent value={value} packed={PACKED_FIELDS.has(col.field ?? '')} />
                        )}
                      </td>
                    );
                  })}
                  {editable && (
                    <td className={cn(STICKY_END, 'whitespace-nowrap border-b-gray-100 bg-white py-1 text-right')}>
                      <RowActions row={row} onDelete={onDelete} onUndo={onUndo} onDuplicate={() => add(row)} />
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {pageCount > 1 && (
        <div className="flex items-center justify-end gap-2 text-xs text-gray-500">
          <span>
            Rows {(pageIndex * PAGE_SIZE + 1).toLocaleString()}–{Math.min((pageIndex + 1) * PAGE_SIZE, visible.length).toLocaleString()} of{' '}
            {visible.length.toLocaleString()} · Page {pageIndex + 1} of {pageCount}
          </span>
          <Button size="sm" variant="outline" disabled={pageIndex === 0} onClick={() => setPage(0)}>First</Button>
          <Button size="sm" variant="outline" disabled={pageIndex === 0} onClick={() => setPage(pageIndex - 1)}>Previous</Button>
          <Button size="sm" variant="outline" disabled={pageIndex >= pageCount - 1} onClick={() => setPage(pageIndex + 1)}>Next</Button>
          <Button size="sm" variant="outline" disabled={pageIndex >= pageCount - 1} onClick={() => setPage(pageCount - 1)}>Last</Button>
        </div>
      )}
    </div>
  );
}

function CellContent({ value, packed }: { value: string; packed: boolean }) {
  if (!value) return <span className="text-gray-300">—</span>;
  if (!packed) {
    const identifier = value.length <= 40 && !/\s/.test(value);
    return <span className={identifier ? 'whitespace-nowrap' : 'break-words'}>{value}</span>;
  }
  return (
    <span className="flex flex-wrap gap-1">
      {splitPacked(value).map((atom, i) => (
        <span key={`${atom}-${i}`} className="rounded border border-gray-200 bg-gray-50 px-1 text-xs text-gray-700">
          {atom}
        </span>
      ))}
    </span>
  );
}

/** Inline editor: Enter saves, Escape cancels, leaving the field saves. */
function CellEditor({ initial, label, packed, onDone }: {
  initial: string;
  label: string;
  packed: boolean;
  onDone: (value: string | null, refocus: boolean) => void;
}) {
  const [draft, setDraft] = useState(initial);
  const finished = useRef(false);
  const finish = (value: string | null, refocus: boolean) => {
    if (finished.current) return;
    finished.current = true;
    onDone(value, refocus);
  };
  return (
    <div className={packed ? 'min-w-[18rem]' : 'min-w-[10rem]'}>
      <input
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            finish(draft, true);
          } else if (e.key === 'Escape') {
            e.preventDefault();
            finish(null, true);
          }
        }}
        onBlur={() => finish(draft, false)}
        aria-label={`Edit ${label}`}
        className="h-7 w-full rounded border border-primary bg-white px-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
      />
      {packed && <p className="mt-0.5 text-[11px] text-gray-500">Separate values with “;”. Enter saves, Esc cancels.</p>}
    </div>
  );
}

function RowStatus({ row }: { row: GridRow }) {
  const pending = row.pendingRequestIds;
  return (
    <div className="flex flex-col items-start gap-1">
      {row.state === 'modified' && <Badge tone="amber">Edited</Badge>}
      {row.state === 'new' && <Badge tone="green">New</Badge>}
      {row.state === 'deleted' && <Badge tone="red">Deleted</Badge>}
      {row.stale && (
        <Badge tone="red" title="Someone saved this row after you staged your change. Committing will ask what to do.">
          Changed since staged
        </Badge>
      )}
      {pending.map((id, i) => (
        <Link
          key={id}
          to={`/requests/${id}`}
          title="An open access request will change this row"
          className="inline-flex items-center gap-1 whitespace-nowrap rounded border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-xs text-indigo-800 hover:bg-indigo-100"
        >
          <Clock className="h-3 w-3" /> Pending request{pending.length > 1 ? ` ${i + 1}` : ''}
        </Link>
      ))}
    </div>
  );
}

function RowActions({ row, onDelete, onUndo, onDuplicate }: {
  row: GridRow;
  onDelete: (row: GridRow) => void;
  onUndo: (row: GridRow) => void;
  onDuplicate: () => void;
}) {
  const btn = 'inline-flex h-7 w-7 items-center justify-center rounded text-gray-500 hover:bg-gray-100 hover:text-gray-900';
  return (
    <span className="inline-flex gap-0.5">
      {row.state !== 'deleted' && (
        <button type="button" className={btn} onClick={onDuplicate} aria-label={`Duplicate row ${row.number}`} title="Duplicate as a new row">
          <Copy className="h-3.5 w-3.5" />
        </button>
      )}
      {(row.state === 'modified' || row.state === 'deleted') && (
        <button type="button" className={btn} onClick={() => onUndo(row)} aria-label={`Undo changes to row ${row.number}`} title="Undo">
          <Undo2 className="h-3.5 w-3.5" />
        </button>
      )}
      {row.state === 'new' ? (
        <button type="button" className={btn} onClick={() => onDelete(row)} aria-label={`Remove new row ${row.number}`} title="Remove this new row">
          <X className="h-3.5 w-3.5" />
        </button>
      ) : (
        row.state !== 'deleted' && (
          <button type="button" className={cn(btn, 'hover:text-red-700')} onClick={() => onDelete(row)} aria-label={`Delete row ${row.number}`} title="Delete row">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )
      )}
    </span>
  );
}
