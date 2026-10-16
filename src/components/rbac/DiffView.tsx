import { useState, type ReactNode } from 'react';
import { ArrowRight } from 'lucide-react';
import type { RbacRowDiff, RbacRowSnapshot, RbacSheetInfo } from '../../types/rbac';
import { Button } from '../ui/button';
import { Badge, type Tone } from './ui';
import { FIELD_LABELS, PACKED_FIELDS, fieldLabel, securableSummary, splitPacked } from './utils';

const OP_LABELS: Record<RbacRowDiff['op'], { label: string; tone: Tone }> = {
  create: { label: 'Added row', tone: 'green' },
  update: { label: 'Edited', tone: 'amber' },
  delete: { label: 'Deleted', tone: 'red' },
  restore: { label: 'Restored', tone: 'blue' },
};

const PAGE = 100;

interface DiffViewProps {
  diffs: RbacRowDiff[];
  sheets?: RbacSheetInfo[];
  /** Extra text for a diff's header, e.g. which staged row it is. */
  context?: (diff: RbacRowDiff, index: number) => ReactNode;
}

/** Row-level diffs, showing before → after for each field that changed. */
export function DiffView({ diffs, sheets, context }: DiffViewProps) {
  const [shown, setShown] = useState(PAGE);
  if (diffs.length === 0) return <p className="text-sm text-gray-500">No row changes.</p>;
  const bySheet = new Map((sheets ?? []).map((s) => [s.name, s]));
  return (
    <div className="space-y-2">
      {diffs.slice(0, shown).map((diff, i) => (
        <DiffCard key={`${diff.rule_id ?? 'new'}-${i}`} diff={diff} sheet={bySheet.get(diff.sheet)} context={context?.(diff, i)} />
      ))}
      {diffs.length > shown && (
        <div className="flex items-center gap-3 text-xs text-gray-500">
          Showing {shown} of {diffs.length} rows.
          <Button size="sm" variant="outline" onClick={() => setShown((n) => n + PAGE)}>Show more</Button>
        </div>
      )}
    </div>
  );
}

interface FieldChange {
  key: string;
  label: string;
  packed: boolean;
  before: string;
  after: string;
}

function snapValue(snap: RbacRowSnapshot | null, key: string, extra: boolean): string {
  if (!snap) return '';
  if (extra) return snap.extra?.[key] ?? '';
  return (snap[key as keyof RbacRowSnapshot] as string | undefined) ?? '';
}

function fieldChanges(diff: RbacRowDiff, sheet?: RbacSheetInfo): FieldChange[] {
  const { before, after, op } = diff;
  const out: FieldChange[] = [];
  const consider = (key: string, label: string, extra: boolean) => {
    const b = snapValue(before, key, extra);
    const a = snapValue(after, key, extra);
    const include = op === 'update' ? a !== b : op === 'delete' ? !!b : !!a;
    if (include) out.push({ key: extra ? `x${key}` : key, label, packed: !extra && PACKED_FIELDS.has(key), before: b, after: a });
  };
  for (const field of Object.keys(FIELD_LABELS)) consider(field, fieldLabel(field, sheet), false);
  const extraKeys = new Set([...Object.keys(before?.extra ?? {}), ...Object.keys(after?.extra ?? {})]);
  for (const key of [...extraKeys].sort((x, y) => Number(x) - Number(y))) consider(key, fieldLabel(key, sheet), true);
  return out;
}

function DiffCard({ diff, sheet, context }: { diff: RbacRowDiff; sheet?: RbacSheetInfo; context?: ReactNode }) {
  const op = OP_LABELS[diff.op] ?? { label: diff.op, tone: 'gray' as Tone };
  const changes = fieldChanges(diff, sheet);
  const what = securableSummary(diff.after ?? diff.before);
  return (
    <div className="rounded-md border border-gray-200 bg-white">
      <div className="flex flex-wrap items-center gap-2 border-b border-gray-100 px-3 py-2 text-xs">
        <Badge tone={op.tone}>{op.label}</Badge>
        <span className="font-medium text-gray-800">{diff.sheet}</span>
        {diff.environment && <span className="text-gray-500">{diff.environment}</span>}
        {what && <span className="truncate text-gray-500" title={what}>{what}</span>}
        {context && <span className="ml-auto text-gray-500">{context}</span>}
      </div>
      {changes.length === 0 ? (
        <p className="px-3 py-2 text-xs text-gray-500">No field values changed.</p>
      ) : (
        <table className="w-full text-xs">
          <tbody>
            {changes.map((c) => (
              <tr key={c.key} className="border-t border-gray-50 align-top first:border-t-0">
                <th scope="row" className="w-40 px-3 py-1.5 text-left font-medium text-gray-500">{c.label}</th>
                <td className="px-3 py-1.5">
                  {c.packed ? <AtomDiff op={diff.op} before={c.before} after={c.after} /> : <ValueDiff op={diff.op} before={c.before} after={c.after} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ValueDiff({ op, before, after }: { op: RbacRowDiff['op']; before: string; after: string }) {
  if (op === 'delete') return <span className="text-red-700 line-through">{before}</span>;
  if (op !== 'update') return <span className="text-green-800">{after}</span>;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className={before ? 'text-red-700 line-through' : 'italic text-gray-400'}>{before || 'empty'}</span>
      <ArrowRight className="h-3 w-3 text-gray-400" aria-label="becomes" />
      <span className={after ? 'text-green-800' : 'italic text-gray-400'}>{after || 'empty'}</span>
    </span>
  );
}

/** A packed cell as chips: added values in green, removed ones struck through. */
function AtomDiff({ op, before, after }: { op: RbacRowDiff['op']; before: string; after: string }) {
  const b = splitPacked(before);
  const a = splitPacked(after);
  const inBefore = new Set(b);
  const inAfter = new Set(a);
  const chips: Array<{ value: string; kind: 'same' | 'added' | 'removed' }> =
    op === 'delete'
      ? b.map((value) => ({ value, kind: 'removed' }))
      : [
          ...a.map((value) => ({ value, kind: op === 'update' && inBefore.has(value) ? ('same' as const) : ('added' as const) })),
          ...(op === 'update' ? b.filter((v) => !inAfter.has(v)).map((value) => ({ value, kind: 'removed' as const })) : []),
        ];
  const orderOnly = op === 'update' && chips.every((c) => c.kind === 'same');
  return (
    <span className="flex flex-wrap items-center gap-1">
      {chips.map((c, i) => (
        <span
          key={`${c.value}-${i}`}
          className={
            c.kind === 'added'
              ? 'rounded border border-green-200 bg-green-50 px-1 text-green-800'
              : c.kind === 'removed'
                ? 'rounded border border-red-200 bg-red-50 px-1 text-red-700 line-through'
                : 'rounded border border-gray-200 bg-gray-50 px-1 text-gray-600'
          }
          title={c.kind === 'added' ? 'Added' : c.kind === 'removed' ? 'Removed' : undefined}
        >
          {c.kind === 'added' && op === 'update' ? '+ ' : ''}
          {c.value}
        </span>
      ))}
      {orderOnly && <span className="text-gray-400">(order or spacing changed: was “{before}”)</span>}
    </span>
  );
}
