import type { ReactNode } from 'react';
import { Lock } from 'lucide-react';
import type { RbacPlane, RbacSheetInfo } from '../../types/rbac';
import { cn } from '../../lib/utils';
import { PLANE_GROUP_LABELS, PLANE_ORDER } from './utils';

/** Picks a managed sheet in two steps: the type of rule, then the environment. */
export function SheetPicker({ sheets, selected, onSelect }: {
  sheets: RbacSheetInfo[];
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  const current = sheets.find((s) => s.name === selected) ?? null;
  const planes = PLANE_ORDER.filter((plane) => sheets.some((s) => s.plane === plane));
  const inPlane = current ? sheets.filter((s) => s.plane === current.plane) : [];

  const selectPlane = (plane: RbacPlane) => {
    if (plane === current?.plane) return;
    const candidates = sheets.filter((s) => s.plane === plane);
    const next = candidates.find((s) => s.environment && s.environment === current?.environment) ?? candidates[0];
    if (next) onSelect(next.name);
  };

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
      <Segmented label="Type">
        {planes.map((plane) => (
          <Segment key={plane} active={plane === current?.plane} onClick={() => selectPlane(plane)}>
            {PLANE_GROUP_LABELS[plane]}
          </Segment>
        ))}
      </Segmented>
      {inPlane.length > 1 || inPlane[0]?.environment ? (
        <Segmented label="Environment">
          {inPlane.map((s) => (
            <Segment
              key={s.name}
              active={s.name === selected}
              onClick={() => onSelect(s.name)}
              title={`${s.name}: ${s.row_count.toLocaleString()} rows${s.editable ? '' : ' (view only for you)'}`}
            >
              {s.environment ?? s.name}
              <span className="text-[11px] text-gray-400">{s.row_count.toLocaleString()}</span>
              {!s.editable && <Lock className="h-3 w-3 text-gray-400" aria-label="View only" />}
            </Segment>
          ))}
        </Segmented>
      ) : (
        current && <span className="text-xs text-gray-500">One sheet for every environment</span>
      )}
    </div>
  );
}

function Segmented({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-medium text-gray-500">{label}</span>
      <div role="group" aria-label={label} className="inline-flex rounded-md border border-gray-200 bg-gray-50 p-0.5">
        {children}
      </div>
    </div>
  );
}

function Segment({ active, onClick, title, children }: {
  active: boolean;
  onClick: () => void;
  title?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      title={title}
      className={cn(
        'flex items-center gap-1.5 rounded px-2 py-0.5 text-[13px] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
        active
          ? 'bg-white font-medium text-primary shadow-sm ring-1 ring-gray-300'
          : 'text-gray-600 hover:text-gray-900',
      )}
    >
      {children}
    </button>
  );
}
