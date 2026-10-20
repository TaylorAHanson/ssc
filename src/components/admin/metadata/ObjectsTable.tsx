import { useMemo } from 'react';
import {
  FlexRender,
  createColumnHelper,
  rowSelectionFeature,
  rowSortingFeature,
  tableFeatures,
  useTable,
  type RowSelectionState,
  type SortingState,
  type Updater,
} from '@tanstack/react-table';
import { ArrowDown, ArrowUp, ArrowUpDown, PencilLine } from 'lucide-react';
import type { ObjectSummary, SortField } from '../../../types/metadata';
import { cn } from '../../../lib/utils';
import { Badge } from '../../rbac/ui';
import { ApplyBadge, DriftBadge, EnvBadge, StateBadge } from './badges';
import { TYPE_LABELS, displayTags, formatTime, objectLabel, relativeTime } from './format';
import { TagList } from './TagChips';

const features = tableFeatures({ rowSortingFeature, rowSelectionFeature });
const helper = createColumnHelper<typeof features, ObjectSummary>();

const SORTABLE: Record<string, SortField> = {
  name: 'name',
  securable_type: 'securable_type',
  catalog: 'catalog',
  drift_detected_at: 'drift_detected_at',
  updated_at: 'updated_at',
  last_applied_at: 'last_applied_at',
};

const apply = <T,>(updater: Updater<T>, current: T): T =>
  typeof updater === 'function' ? (updater as (old: T) => T)(current) : updater;

interface ObjectsTableProps {
  items: ObjectSummary[];
  sort: SortField;
  order: 'asc' | 'desc';
  onSort: (sort: SortField, order: 'asc' | 'desc') => void;
  selection: Record<string, ObjectSummary>;
  onSelectionChange: (next: Record<string, ObjectSummary>) => void;
  stagedKeys: Set<string>;
  exemptKeys: string[];
  onOpen: (s: ObjectSummary) => void;
  onStage: (s: ObjectSummary) => void;
}

/** One page of search results; sorting happens on the server, selection spans pages. */
export function ObjectsTable({ items, sort, order, onSort, selection, onSelectionChange, stagedKeys, exemptKeys, onOpen, onStage }: ObjectsTableProps) {
  const columns = useMemo(
    () =>
      helper.columns([
        helper.display({ id: 'select', enableSorting: false, header: () => null, cell: () => null }),
        helper.accessor((s) => s.name, {
          id: 'name',
          header: 'Name',
          cell: (info) => {
            const s = info.row.original;
            return (
              <div className="min-w-0">
                <button
                  type="button"
                  onClick={() => onOpen(s)}
                  className="max-w-full truncate text-left font-medium text-gray-900 hover:text-primary hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                  title={`Open ${objectLabel(s.name, s.column)}`}
                >
                  {objectLabel(s.name, s.column)}
                </button>
                {(s.comment ?? s.observed_comment) && (
                  <p className="max-w-md truncate text-xs text-gray-500" title={(s.comment ?? s.observed_comment) || undefined}>
                    {s.managed ? s.comment : s.observed_comment}
                  </p>
                )}
              </div>
            );
          },
        }),
        helper.accessor((s) => s.securable_type, {
          id: 'securable_type',
          header: 'Type',
          cell: (info) => {
            const s = info.row.original;
            return (
              <span className="whitespace-nowrap text-xs text-gray-600">
                {TYPE_LABELS[s.securable_type]}
                {s.data_type && <span className="ml-1 font-mono text-gray-400">{s.data_type}</span>}
              </span>
            );
          },
        }),
        helper.accessor((s) => s.catalog, {
          id: 'catalog',
          header: 'Env',
          cell: (info) => <EnvBadge env={info.row.original.environment} />,
        }),
        helper.display({
          id: 'tags',
          header: 'Tags',
          enableSorting: false,
          cell: (info) => <TagList tags={displayTags(info.row.original)} exemptKeys={exemptKeys} max={4} />,
        }),
        helper.accessor((s) => s.drift_detected_at ?? '', {
          id: 'drift_detected_at',
          header: 'Status',
          cell: (info) => {
            const s = info.row.original;
            return (
              <span className="flex flex-wrap gap-1">
                <StateBadge state={s.object_state} managed={s.managed} />
                {s.drift && <DriftBadge title={s.drift_detected_at ? `Drift noticed ${relativeTime(s.drift_detected_at)}` : undefined} />}
                {s.last_apply_status === 'failed' || s.last_apply_status === 'pending' ? <ApplyBadge status={s.last_apply_status} /> : null}
                {stagedKeys.has(s.fqn_key) && <Badge tone="indigo">Staged</Badge>}
              </span>
            );
          },
        }),
        helper.accessor((s) => s.updated_at ?? '', {
          id: 'updated_at',
          header: 'Updated',
          cell: (info) => (
            <span className="whitespace-nowrap text-xs text-gray-500" title={formatTime(info.row.original.updated_at)}>
              {relativeTime(info.row.original.updated_at) || '—'}
            </span>
          ),
        }),
        helper.accessor((s) => s.last_applied_at ?? '', {
          id: 'last_applied_at',
          header: 'Applied',
          cell: (info) => (
            <span className="whitespace-nowrap text-xs text-gray-500" title={formatTime(info.row.original.last_applied_at)}>
              {relativeTime(info.row.original.last_applied_at) || '—'}
            </span>
          ),
        }),
        helper.display({
          id: 'actions',
          header: () => <span className="sr-only">Actions</span>,
          enableSorting: false,
          cell: (info) => {
            const s = info.row.original;
            return (
              <button
                type="button"
                onClick={() => onStage(s)}
                className="inline-flex h-7 w-7 items-center justify-center rounded text-gray-500 hover:bg-gray-100 hover:text-gray-900"
                aria-label={`Edit ${objectLabel(s.name, s.column)}`}
                title="Stage edits for this object"
              >
                <PencilLine className="h-3.5 w-3.5" />
              </button>
            );
          },
        }),
      ]),
    [exemptKeys, onOpen, onStage, stagedKeys],
  );

  const sorting: SortingState = [{ id: sort, desc: order === 'desc' }];
  const rowSelection: RowSelectionState = Object.fromEntries(items.filter((s) => selection[s.fqn_key]).map((s) => [s.fqn_key, true as const]));

  const table = useTable({
    features,
    columns,
    data: items,
    getRowId: (row) => row.fqn_key,
    state: { sorting, rowSelection },
    manualSorting: true,
    enableMultiSort: false,
    enableSortingRemoval: false,
    onSortingChange: (updater) => {
      const next = apply(updater, sorting)[0];
      const field = next ? SORTABLE[next.id] : undefined;
      if (field) onSort(field, next.desc ? 'desc' : 'asc');
    },
    onRowSelectionChange: (updater) => {
      const next = apply(updater, rowSelection);
      const out = { ...selection };
      items.forEach((s) => {
        if (next[s.fqn_key]) out[s.fqn_key] = s;
        else delete out[s.fqn_key];
      });
      onSelectionChange(out);
    },
  });

  const rows = table.getRowModel().rows;
  const pageSelected = rows.filter((r) => r.getIsSelected()).length;
  const allOnPage = rows.length > 0 && pageSelected === rows.length;

  const togglePage = () => {
    const out = { ...selection };
    items.forEach((s) => {
      if (allOnPage) delete out[s.fqn_key];
      else out[s.fqn_key] = s;
    });
    onSelectionChange(out);
  };

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
      <table className="w-full border-separate border-spacing-0 text-sm">
        <thead className="sticky top-0 z-10 bg-gray-50 text-left text-xs text-gray-600">
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                if (header.column.id === 'select') {
                  return (
                    <th key={header.id} scope="col" className="w-8 border-b border-gray-200 px-2 py-2">
                      <input
                        type="checkbox"
                        checked={allOnPage}
                        ref={(el) => {
                          if (el) el.indeterminate = pageSelected > 0 && !allOnPage;
                        }}
                        onChange={togglePage}
                        disabled={!rows.length}
                        aria-label={allOnPage ? 'Deselect every object on this page' : 'Select every object on this page'}
                        className="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
                      />
                    </th>
                  );
                }
                const canSort = header.column.getCanSort();
                const sorted = header.column.getIsSorted();
                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : undefined}
                    className="whitespace-nowrap border-b border-gray-200 px-2 py-2 font-medium"
                  >
                    {canSort ? (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className="inline-flex items-center gap-1 hover:text-gray-900"
                        title="Sort"
                      >
                        <FlexRender header={header} />
                        {sorted === 'asc' ? (
                          <ArrowUp className="h-3 w-3" />
                        ) : sorted === 'desc' ? (
                          <ArrowDown className="h-3 w-3" />
                        ) : (
                          <ArrowUpDown className="h-3 w-3 text-gray-300" />
                        )}
                      </button>
                    ) : (
                      <FlexRender header={header} />
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {rows.map((row) => {
            const s = row.original;
            const selected = row.getIsSelected();
            return (
              <tr key={row.id} className={cn('align-top', selected && 'bg-indigo-50/60', !s.managed && 'text-gray-600')}>
                {row.getAllCells().map((cell) =>
                  cell.column.id === 'select' ? (
                    <td key={cell.id} className="border-b border-gray-100 px-2 py-2">
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={(e) => row.toggleSelected(e.target.checked)}
                        aria-label={`Select ${objectLabel(s.name, s.column)}`}
                        className="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
                      />
                    </td>
                  ) : (
                    <td key={cell.id} className={cn('border-b border-gray-100 px-2 py-2', cell.column.id === 'name' && 'max-w-[22rem]')}>
                      <FlexRender cell={cell} />
                    </td>
                  ),
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
