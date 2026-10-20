import { useId, useState } from 'react';
import { Switch } from '../../ui/switch';
import type { Facet, ObjectState, SecurableType } from '../../../types/metadata';
import { TYPE_LABELS } from './format';
import { useObjectsQuery, type ObjectsQuery } from './objectsQuery';

const STATE_LABELS: Record<ObjectState, string> = { exists: 'Exists', pending: 'Not created yet', orphaned: 'Missing from Unity Catalog' };

type ListField = 'catalogs' | 'securable_types' | 'environments' | 'object_states';

const GROUPS: { facet: string; field: ListField; title: string; label?: (v: string) => string }[] = [
  { facet: 'catalog', field: 'catalogs', title: 'Catalog' },
  { facet: 'securable_type', field: 'securable_types', title: 'Type', label: (v) => TYPE_LABELS[v as SecurableType] ?? v },
  { facet: 'environment', field: 'environments', title: 'Environment' },
  { facet: 'object_state', field: 'object_states', title: 'State', label: (v) => STATE_LABELS[v as ObjectState] ?? v },
];

const TOGGLES: { field: 'editable_only' | 'drift_only' | 'include_columns' | 'include_unmanaged'; label: string; hint: string }[] = [
  { field: 'editable_only', label: 'Editable by me', hint: 'Only objects you can change without approval.' },
  { field: 'drift_only', label: 'Drifted only', hint: 'Objects whose Unity Catalog tags or description differ from what’s declared here.' },
  { field: 'include_columns', label: 'Include columns', hint: 'Search columns as well as catalogs, schemas, tables and views.' },
  { field: 'include_unmanaged', label: 'Include unmanaged', hint: 'Also show objects Unity Catalog has but nobody manages here yet.' },
];

export function FacetSidebar({ facets }: { facets: Record<string, Facet[]> | undefined }) {
  const query = useObjectsQuery();
  return (
    <aside className="space-y-5" aria-label="Filters">
      <div className="space-y-2.5">
        {TOGGLES.map((t) => (
          <Toggle key={t.field} label={t.label} hint={t.hint} checked={query[t.field]} onChange={(v) => query.update({ [t.field]: v } as Partial<ObjectsQuery>)} />
        ))}
      </div>
      {GROUPS.map((g) => (
        <FacetGroup
          key={g.facet}
          title={g.title}
          facets={facets?.[g.facet] ?? []}
          selected={query[g.field] as string[]}
          label={g.label}
          onChange={(values) => query.update({ [g.field]: values } as Partial<ObjectsQuery>)}
        />
      ))}
      <FacetGroup
        title="Tag key"
        facets={facets?.tag_key ?? []}
        selected={query.tags.filter((t) => !t.includes('=') && !t.startsWith('!'))}
        onChange={(keys) => query.update({ tags: [...query.tags.filter((t) => t.includes('=') || t.startsWith('!')), ...keys] })}
        hint="Objects that have the tag, with any value."
      />
    </aside>
  );
}

function Toggle({ label, hint, checked, onChange }: { label: string; hint: string; checked: boolean; onChange: (v: boolean) => void }) {
  const id = useId();
  return (
    <div className="flex items-start gap-2">
      <Switch id={id} checked={checked} onCheckedChange={onChange} aria-describedby={`${id}-hint`} className="mt-0.5 scale-75" />
      <div className="min-w-0">
        <label htmlFor={id} className="cursor-pointer text-sm text-gray-800">
          {label}
        </label>
        <p id={`${id}-hint`} className="text-xs text-gray-500">
          {hint}
        </p>
      </div>
    </div>
  );
}

const COLLAPSED = 8;

function FacetGroup({ title, facets, selected, onChange, label, hint }: {
  title: string;
  facets: Facet[];
  selected: string[];
  onChange: (values: string[]) => void;
  label?: (v: string) => string;
  hint?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  // Keep selected values visible even when the current results have none of them.
  const all = [...facets, ...selected.filter((s) => !facets.some((f) => f.value === s)).map((value) => ({ value, count: 0 }))];
  if (!all.length) return null;
  const shown = expanded ? all : all.slice(0, COLLAPSED);
  const toggle = (value: string) => onChange(selected.includes(value) ? selected.filter((s) => s !== value) : [...selected, value]);
  return (
    <fieldset>
      <legend className="mb-1.5 flex w-full items-center justify-between text-xs font-semibold uppercase tracking-wide text-gray-500">
        {title}
        {selected.length > 0 && (
          <button type="button" onClick={() => onChange([])} className="font-normal normal-case tracking-normal text-primary hover:underline">
            Clear
          </button>
        )}
      </legend>
      {hint && <p className="mb-1 text-xs text-gray-500">{hint}</p>}
      <ul className="space-y-0.5">
        {shown.map((f) => (
          <li key={f.value}>
            <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-sm hover:bg-gray-50">
              <input
                type="checkbox"
                checked={selected.includes(f.value)}
                onChange={() => toggle(f.value)}
                className="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
              />
              <span className="min-w-0 flex-1 truncate" title={f.value}>
                {label ? label(f.value) : f.value}
              </span>
              <span className="text-xs tabular-nums text-gray-400">{f.count.toLocaleString()}</span>
            </label>
          </li>
        ))}
      </ul>
      {all.length > COLLAPSED && (
        <button type="button" onClick={() => setExpanded(!expanded)} className="mt-1 text-xs text-primary hover:underline">
          {expanded ? 'Show fewer' : `Show all ${all.length}`}
        </button>
      )}
    </fieldset>
  );
}
