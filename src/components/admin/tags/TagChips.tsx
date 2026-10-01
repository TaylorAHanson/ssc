import type { EditTarget } from './tagModel';
import { buildDesired } from './tagModel';

// A target's tags at a glance, with pending edits marked: green = new,
// amber = new value, struck-through red = removed.
export function TagChips({ target, max = 8 }: { target: EditTarget; max?: number }) {
  const desired = buildDesired(target.rows);
  const chips: { key: string; value: string; state: 'same' | 'new' | 'changed' | 'removed' }[] = [];
  for (const [k, v] of Object.entries(desired)) {
    const state = !(k in target.original) ? 'new' : target.original[k] !== v ? 'changed' : 'same';
    chips.push({ key: k, value: v, state });
  }
  for (const [k, v] of Object.entries(target.original)) {
    if (!(k in desired)) chips.push({ key: k, value: v, state: 'removed' });
  }

  if (chips.length === 0) return <span className="text-[11px] text-gray-400 italic">No tags</span>;
  // Pending edits first, so they stay visible when the list is truncated.
  chips.sort((a, b) => Number(a.state === 'same') - Number(b.state === 'same'));

  const shown = chips.slice(0, max);
  return (
    <span className="flex flex-wrap items-center gap-1">
      {shown.map((c) => (
        <span
          key={`${c.key}:${c.state}`}
          title={`${c.key} = ${c.value}`}
          className={`inline-flex max-w-[16rem] items-center rounded border px-1.5 py-0.5 font-mono text-[11px] leading-none ${
            c.state === 'new'
              ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
              : c.state === 'changed'
              ? 'border-amber-200 bg-amber-50 text-amber-800'
              : c.state === 'removed'
              ? 'border-rose-200 bg-rose-50 text-rose-700 line-through'
              : 'border-gray-200 bg-gray-50 text-gray-700'
          }`}
        >
          <span className="truncate">
            {c.key}
            {c.value ? <span className="text-gray-400">=</span> : null}
            {c.value}
          </span>
        </span>
      ))}
      {chips.length > max && <span className="text-[11px] text-gray-500">+{chips.length - max} more</span>}
    </span>
  );
}
