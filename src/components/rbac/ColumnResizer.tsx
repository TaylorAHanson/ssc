import type { ReactNode } from 'react';
import { Columns3 } from 'lucide-react';
import { cn } from '../../lib/utils';
import type { ColumnWidths } from './useResizableColumns';

/**
 * A header cell with a resize handle. Its label truncates only under the fixed layout: in the
 * automatic one, truncating content lets the browser squeeze the column below its text.
 */
export function ResizableTh({ columns, id, label, className, children }: {
  columns: ColumnWidths;
  id: string;
  label: string;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <th scope="col" data-col={id} className={cn('relative whitespace-nowrap py-1.5 pl-2 pr-3 font-medium', className)}>
      <span className={cn(columns.active && 'block truncate')} title={label}>{children ?? label}</span>
      <ColumnResizer columns={columns} id={id} label={label} />
    </th>
  );
}

/** The drag handle on a header cell's right edge; the cell needs `relative`. */
export function ColumnResizer({ columns, id, label }: { columns: ColumnWidths; id: string; label: string }) {
  return (
    <span
      role="separator"
      aria-orientation="vertical"
      aria-label={`Resize the ${label || 'unnamed'} column`}
      title="Drag to resize. Double-click to reset."
      tabIndex={0}
      {...columns.handleProps(id)}
      className={cn(
        'absolute inset-y-0 right-0 z-[1] w-1.5 cursor-col-resize touch-none select-none focus:outline-none',
        'after:absolute after:inset-y-1 after:right-0 after:w-px after:bg-gray-200',
        'hover:after:w-0.5 hover:after:bg-primary focus-visible:after:w-0.5 focus-visible:after:bg-primary',
      )}
    />
  );
}

/** Fixes each column at its width once one has been resized; renders nothing before that. */
export function ColGroup({ columns }: { columns: ColumnWidths }) {
  if (!columns.active) return null;
  return (
    <colgroup>
      {columns.ids.map((id) => (
        <col key={id} style={{ width: columns.widthOf(id) }} />
      ))}
    </colgroup>
  );
}

export function ResetColumnsButton({ columns, className }: { columns: ColumnWidths; className?: string }) {
  if (!columns.customized) return null;
  return (
    <button
      type="button"
      onClick={columns.resetAll}
      title="Go back to the automatic column widths"
      className={cn('inline-flex items-center gap-1 whitespace-nowrap text-xs text-gray-500 hover:text-gray-900', className)}
    >
      <Columns3 className="h-3.5 w-3.5" aria-hidden="true" /> Reset columns
    </button>
  );
}
