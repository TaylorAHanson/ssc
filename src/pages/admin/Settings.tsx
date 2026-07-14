import { useEffect, useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Loader2, Save, RotateCcw, Lock, Info, Plus, Trash2 } from 'lucide-react';
import { getSettings, updateSettings } from '../../services/api';
import type {
  SettingsState,
  SettingField,
  ReadonlySettingField,
  CollectionRow,
  SettingWriteValue,
  SelfServiceCatalog,
  CommunityLinksCatalog,
  EmbeddedApp,
} from '../../services/api';
import { useBrandingStore } from '../../stores/brandingStore';
import { useRequestStore } from '../../stores/requestStore';
import { Users } from './Users';
import {
  StringListField,
  SelfServiceCenterEditor,
  CommunityLinksEditor,
  EmbeddedAppsEditor,
} from './catalogEditors';

const ROLES_GROUP = 'Roles & Access';
const INFRA_GROUP = 'Infrastructure';

type FieldValue = SettingWriteValue;

export const Settings = () => {
  const fetchBranding = useBrandingStore((s) => s.fetchBranding);

  const [state, setState] = useState<SettingsState | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, FieldValue>>({});
  const [activeGroup, setActiveGroup] = useState<string>('Branding & Appearance');
  const [isSaving, setIsSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const load = async () => {
    setIsLoading(true);
    try {
      const s = await getSettings();
      setState(s);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load settings');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Ordered list of groups for the left sub-nav. Editable groups come from the
  // backend's group_order (falling back to discovery), then Roles & Access and
  // the read-only Infrastructure section.
  const groups = useMemo(() => {
    if (!state) return [] as string[];
    const editableGroups = new Set(state.fields.map((f) => f.group));
    const ordered = state.group_order.filter((g) => editableGroups.has(g));
    for (const g of editableGroups) {
      if (!ordered.includes(g)) ordered.push(g);
    }
    return [...ordered, ROLES_GROUP, INFRA_GROUP];
  }, [state]);

  const dirtyCount = Object.keys(draft).length;

  const valueOf = (f: SettingField): FieldValue => {
    if (f.key in draft) return draft[f.key];
    if (f.type === 'collection' || f.type === 'string_list') return (f.value ?? []) as FieldValue;
    if (f.type === 'catalog') {
      if (f.kind === 'embedded_apps') return (f.value ?? []) as FieldValue;
      return (f.value ?? { enabled: true, categories: [] }) as FieldValue;
    }
    return (f.value ?? (f.type === 'bool' ? false : '')) as FieldValue;
  };

  const setValue = (key: string, value: FieldValue) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setMessage(null);
  };

  const handleSave = async () => {
    if (dirtyCount === 0) return;
    setIsSaving(true);
    setMessage(null);
    try {
      const next = await updateSettings(draft);
      setState(next);
      setDraft({});
      // Branding, feature flags, nav tabs, and the system banner are served via
      // /branding — refresh so colors, brand name, sidebar, and banner reflect
      // the change immediately.
      await fetchBranding();
      await useRequestStore.getState().fetchBannerMessage();
      setMessage({ type: 'success', text: 'Settings saved. Changes are live.' });
      setTimeout(() => setMessage(null), 4000);
    } catch (e) {
      setMessage({ type: 'error', text: e instanceof Error ? e.message : 'Failed to save settings' });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDiscard = () => {
    setDraft({});
    setMessage(null);
  };

  if (isLoading) {
    return (
      <div className="flex justify-center items-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg">
        Error: {error}
      </div>
    );
  }

  const fieldsForGroup = (group: string): SettingField[] =>
    (state?.fields || []).filter((f) => f.group === group);

  return (
    <div className="flex gap-6 min-h-[calc(100vh-240px)]">
      {/* Sub-nav */}
      <div className="w-60 flex-shrink-0">
        <Card className="h-full">
          <CardContent className="p-2">
            <nav className="space-y-1">
              {groups.map((g) => (
                <button
                  key={g}
                  onClick={() => setActiveGroup(g)}
                  className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors flex items-center gap-2 ${
                    activeGroup === g ? 'bg-primary text-white' : 'hover:bg-gray-100 text-gray-700'
                  }`}
                >
                  {g === INFRA_GROUP && <Lock className="w-3.5 h-3.5 opacity-70" />}
                  {g}
                </button>
              ))}
            </nav>
          </CardContent>
        </Card>
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 space-y-4">
        {message && (
          <div
            className={`p-3 rounded-md text-sm ${
              message.type === 'success'
                ? 'bg-green-50 border border-green-200 text-green-800'
                : 'bg-red-50 border border-red-200 text-red-800'
            }`}
          >
            {message.text}
          </div>
        )}

        {activeGroup === ROLES_GROUP ? (
          <Users />
        ) : activeGroup === INFRA_GROUP ? (
          <ReadonlyPanel fields={state?.readonly || []} />
        ) : (
          <>
            <Card>
              <CardHeader className="flex flex-row items-start justify-between gap-4">
                <div>
                  <CardTitle>{activeGroup}</CardTitle>
                  {state?.group_descriptions?.[activeGroup] && (
                    <p className="text-sm text-gray-500 mt-1.5 max-w-2xl leading-relaxed">
                      {state.group_descriptions[activeGroup]}
                    </p>
                  )}
                </div>
                {dirtyCount > 0 && (
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <Button variant="outline" size="sm" onClick={handleDiscard} disabled={isSaving}>
                      <RotateCcw className="w-4 h-4 mr-1" /> Discard
                    </Button>
                    <Button size="sm" onClick={handleSave} disabled={isSaving} className="bg-primary text-white">
                      {isSaving ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}
                      Save {dirtyCount} change{dirtyCount > 1 ? 's' : ''}
                    </Button>
                  </div>
                )}
              </CardHeader>
              <CardContent className="space-y-5">
                {fieldsForGroup(activeGroup).map((f) => {
                  const set = (v: FieldValue) => setValue(f.key, v);
                  if (f.type === 'collection') {
                    return (
                      <CollectionField key={f.key} field={f} value={(valueOf(f) as CollectionRow[]) || []} onChange={set} />
                    );
                  }
                  if (f.type === 'string_list') {
                    return (
                      <StringListField key={f.key} field={f} value={(valueOf(f) as string[]) || []} onChange={set} />
                    );
                  }
                  if (f.type === 'catalog') {
                    if (f.kind === 'self_service') {
                      return (
                        <SelfServiceCenterEditor
                          key={f.key}
                          field={f}
                          value={valueOf(f) as SelfServiceCatalog}
                          onChange={set}
                        />
                      );
                    }
                    if (f.kind === 'community_links') {
                      return (
                        <CommunityLinksEditor
                          key={f.key}
                          field={f}
                          value={valueOf(f) as CommunityLinksCatalog}
                          onChange={set}
                        />
                      );
                    }
                    if (f.kind === 'embedded_apps') {
                      return (
                        <EmbeddedAppsEditor
                          key={f.key}
                          field={f}
                          value={(valueOf(f) as EmbeddedApp[]) || []}
                          onChange={set}
                        />
                      );
                    }
                  }
                  return <FieldRow key={f.key} field={f} value={valueOf(f)} onChange={set} />;
                })}
                {fieldsForGroup(activeGroup).length === 0 && (
                  <p className="text-sm text-gray-500">No settings in this group.</p>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  );
};

function FieldRow({
  field,
  value,
  onChange,
}: {
  field: SettingField;
  value: FieldValue;
  onChange: (value: FieldValue) => void;
}) {
  const isBool = field.type === 'bool';

  return (
    <div className={`flex ${isBool ? 'items-center justify-between' : 'flex-col'} gap-2 pb-4 border-b border-gray-100 last:border-0`}>
      <div className={isBool ? '' : 'space-y-1'}>
        <label className="text-sm font-medium text-gray-800">{field.label}</label>
        {field.help && <p className="text-xs text-gray-500 max-w-2xl">{field.help}</p>}
      </div>

      {isBool ? (
        <button
          type="button"
          role="switch"
          aria-checked={Boolean(value)}
          onClick={() => onChange(!value)}
          className={`relative inline-flex h-6 w-11 flex-shrink-0 items-center rounded-full transition-colors ${
            value ? 'bg-primary' : 'bg-gray-300'
          }`}
        >
          <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
              value ? 'translate-x-6' : 'translate-x-1'
            }`}
          />
        </button>
      ) : field.type === 'color' ? (
        <div className="flex items-center gap-3">
          <input
            type="color"
            value={String(value || '#000000')}
            onChange={(e) => onChange(e.target.value)}
            className="h-9 w-14 rounded border border-gray-200 cursor-pointer bg-white p-1"
          />
          <input
            type="text"
            value={String(value ?? '')}
            onChange={(e) => onChange(e.target.value)}
            className="w-32 px-3 py-2 border rounded-md text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/20 border-gray-200"
          />
        </div>
      ) : field.type === 'int' ? (
        <input
          type="number"
          value={value === '' || value === null ? '' : Number(value)}
          min={field.min}
          max={field.max}
          onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
          className="w-40 px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 border-gray-200"
        />
      ) : field.type === 'select' ? (
        <select
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
          className="w-56 px-3 py-2 border rounded-md text-sm bg-white focus:outline-none focus:ring-2 focus:ring-primary/20 border-gray-200 capitalize"
        >
          {(field.options || []).map((opt) => (
            <option key={opt} value={opt} className="capitalize">
              {opt}
            </option>
          ))}
        </select>
      ) : field.type === 'textarea' ? (
        <textarea
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
          className="w-full px-3 py-2 border rounded-md text-sm resize-y focus:outline-none focus:ring-2 focus:ring-primary/20 border-gray-200"
        />
      ) : field.type === 'cron' ? (
        <input
          type="text"
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
          placeholder="*/30 * * * *  (blank = disabled)"
          spellCheck={false}
          className="w-64 px-3 py-2 border rounded-md text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/20 border-gray-200"
        />
      ) : (
        <input
          type="text"
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
          className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 border-gray-200"
        />
      )}
    </div>
  );
}

function CollectionField({
  field,
  value,
  onChange,
}: {
  field: SettingField;
  value: CollectionRow[];
  onChange: (value: CollectionRow[]) => void;
}) {
  const columns = field.columns || [];
  const rows = value || [];

  const updateCell = (idx: number, key: string, cell: string | number | boolean) => {
    onChange(rows.map((r, i) => (i === idx ? { ...r, [key]: cell } : r)));
  };
  const addRow = () => {
    const blank: CollectionRow = {};
    columns.forEach((c) => {
      blank[c.key] = c.type === 'bool' ? false : '';
    });
    onChange([...rows, blank]);
  };
  const removeRow = (idx: number) => onChange(rows.filter((_, i) => i !== idx));

  return (
    <div className="space-y-3 pb-4 border-b border-gray-100 last:border-0">
      <div className="space-y-1">
        <label className="text-sm font-medium text-gray-800">{field.label}</label>
        {field.help && <p className="text-xs text-gray-500 max-w-2xl leading-relaxed">{field.help}</p>}
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-gray-400 italic">None configured yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-separate border-spacing-0">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c.key} className="text-left font-medium text-gray-500 text-xs px-2 pb-1.5 whitespace-nowrap align-bottom">
                    <div>
                      {c.label}
                      {c.required && <span className="text-red-500 ml-0.5">*</span>}
                    </div>
                    {c.help && <div className="text-[10px] font-normal text-gray-400 normal-case">{c.help}</div>}
                  </th>
                ))}
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row, idx) => (
                <tr key={idx}>
                  {columns.map((c) => (
                    <td key={c.key} className="px-1 py-1 align-top">
                      {c.type === 'bool' ? (
                        <input
                          type="checkbox"
                          checked={Boolean(row[c.key])}
                          onChange={(e) => updateCell(idx, c.key, e.target.checked)}
                          className="h-4 w-4 mt-2"
                        />
                      ) : (
                        <input
                          type={c.type === 'int' ? 'number' : 'text'}
                          value={row[c.key] === null || row[c.key] === undefined ? '' : String(row[c.key])}
                          placeholder={c.placeholder}
                          onChange={(e) =>
                            updateCell(
                              idx,
                              c.key,
                              c.type === 'int' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value
                            )
                          }
                          className="w-full min-w-[9rem] px-2 py-1.5 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 border-gray-200"
                        />
                      )}
                    </td>
                  ))}
                  <td className="px-1 py-1 align-top">
                    <button
                      type="button"
                      onClick={() => removeRow(idx)}
                      className="p-1.5 text-gray-400 hover:text-red-600 transition-colors"
                      title="Remove"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Button variant="outline" size="sm" onClick={addRow}>
        <Plus className="w-4 h-4 mr-1" /> {field.add_label || 'Add row'}
      </Button>
    </div>
  );
}

function ReadonlyPanel({ fields }: { fields: ReadonlySettingField[] }) {
  const grouped = useMemo(() => {
    const map = new Map<string, ReadonlySettingField[]>();
    for (const f of fields) {
      const arr = map.get(f.group) || [];
      arr.push(f);
      map.set(f.group, arr);
    }
    return Array.from(map.entries());
  }, [fields]);

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 bg-blue-50 border border-blue-200 text-blue-800 p-3 rounded-md text-sm">
        <Info className="w-4 h-4 mt-0.5 flex-shrink-0" />
        <span>
          These are managed in <code className="font-mono">databricks.yml</code> and secrets. They are shown for
          reference and take effect only on redeploy/restart.
        </span>
      </div>
      {grouped.map(([group, groupFields]) => (
        <Card key={group}>
          <CardHeader>
            <CardTitle className="text-base">{group}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {groupFields.map((f) => (
              <div key={f.key} className="flex items-center justify-between gap-4 text-sm">
                <span className="text-gray-700">{f.label}</span>
                <span className="font-mono text-gray-500 truncate max-w-md text-right">
                  {f.value === '' || f.value === null ? '—' : String(f.value)}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
