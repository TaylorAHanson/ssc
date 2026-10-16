import { Lock } from 'lucide-react';
import type { RbacSheetInfo } from '../../types/rbac';
import { cn } from '../../lib/utils';
import { PLANE_GROUP_LABELS, PLANE_ORDER } from './utils';

/** The managed sheets, grouped by type, with environment and row count. */
export function SheetPicker({ sheets, selected, onSelect }: {
  sheets: RbacSheetInfo[];
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  const groups = PLANE_ORDER.map((plane) => ({ plane, sheets: sheets.filter((s) => s.plane === plane) })).filter(
    (g) => g.sheets.length > 0,
  );
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-3">
      {groups.map((g) => (
        <div key={g.plane} role="group" aria-label={PLANE_GROUP_LABELS[g.plane]}>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">{PLANE_GROUP_LABELS[g.plane]}</div>
          <div className="flex flex-wrap gap-1.5">
            {g.sheets.map((s) => {
              const active = s.name === selected;
              return (
                <button
                  key={s.name}
                  type="button"
                  aria-pressed={active}
                  onClick={() => onSelect(s.name)}
                  title={s.editable ? undefined : 'View only for you'}
                  className={cn(
                    'flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-left text-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                    active ? 'border-primary bg-primary/5 text-gray-900' : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50',
                  )}
                >
                  <span className="font-mono text-xs">{s.name}</span>
                  {s.environment && <span className="rounded bg-gray-100 px-1.5 text-[11px] text-gray-600">{s.environment}</span>}
                  <span className="text-xs text-gray-500">{s.row_count.toLocaleString()} rows</span>
                  {!s.editable && <Lock className="h-3 w-3 text-gray-400" aria-label="View only" />}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
