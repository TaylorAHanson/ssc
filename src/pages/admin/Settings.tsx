import { useEffect, useMemo, useState, type ReactNode } from 'react';
import cronstrue from 'cronstrue';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import {
  AlertTriangle,
  ArrowRight,
  ChevronRight,
  ExternalLink,
  Info,
  Loader2,
  Plus,
  PowerOff,
  RotateCcw,
  Save,
  Trash2,
} from 'lucide-react';
import { getSettings, updateSettings } from '../../services/api';
import type {
  Capability,
  SettingsState,
  SettingField,
  ReadonlySettingField,
  CollectionRow,
  SettingWriteValue,
  CommunityLinksCatalog,
  EmbeddedApp,
} from '../../services/api';
import { useBrandingStore } from '../../stores/brandingStore';
import { useRequestStore } from '../../stores/requestStore';
import { cn } from '../../lib/utils';
import { Users } from './Users';
import { StringListField, CommunityLinksEditor, EmbeddedAppsEditor } from './catalogEditors';

// Pages rendered by the client rather than from the generic field list. The
// backend's `sections` names them; these must match settings_store.py.
const FEATURES_GROUP = 'Features & Navigation';
const ROLES_GROUP = 'Roles & Access';
// Also shows the read-only, deploy-time settings under its editable fields.
const INFRA_GROUP = 'Infrastructure';

type FieldValue = SettingWriteValue;

export const Settings = () => {
  const fetchBranding = useBrandingStore((s) => s.fetchBranding);

  const [state, setState] = useState<SettingsState | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, FieldValue>>({});
  const [activeGroup, setActiveGroup] = useState<string>('Appearance');
  const [focusCapability, setFocusCapability] = useState<string | null>(null);
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
  }, []);

  const fieldsByKey = useMemo(() => {
    const map = new Map<string, SettingField>();
    for (const f of state?.fields || []) map.set(f.key, f);
    return map;
  }, [state]);

  // Which capability owns each feature/tab key, for requirement checks and the
  // "turn it on" links.
  const capabilityByKey = useMemo(() => {
    const map = new Map<string, Capability>();
    for (const c of state?.capabilities || []) {
      for (const k of c.keys) map.set(k, c);
    }
    return map;
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

  // Current (draft-aware) value of a feature/tab switch. A flag missing from
  // the config counts as on, matching the backend's is_feature_enabled.
  const switchOn = (key: string): boolean => {
    if (key in draft) return Boolean(draft[key]);
    const f = fieldsByKey.get(key);
    return f ? Boolean(f.value) : true;
  };

  // A tab only counts as on while its capability's feature is also on, since
  // the sidebar hides it otherwise.
  const requirementMet = (key: string): boolean => {
    if (!switchOn(key)) return false;
    if (key.startsWith('ui.tabs.')) {
      const feature = capabilityByKey.get(key)?.feature;
      if (feature && !switchOn(feature)) return false;
    }
    return true;
  };

  const unmetRequirements = (f: SettingField): string[] => (f.requires || []).filter((k) => !requirementMet(k));

  const offCapabilities = (keys: string[]): Capability[] => {
    const seen = new Map<string, Capability>();
    for (const k of keys) {
      const c = capabilityByKey.get(k);
      if (c && !seen.has(c.id)) seen.set(c.id, c);
    }
    return Array.from(seen.values());
  };

  const fieldsForGroup = (group: string): SettingField[] =>
    (state?.fields || []).filter((f) => f.group === group);

  // A page is "off" when every one of its settings is waiting on a capability.
  const pageOffCapabilities = (group: string): Capability[] | null => {
    if (group === FEATURES_GROUP || group === ROLES_GROUP) return null;
    const fields = fieldsForGroup(group);
    if (fields.length === 0) return null;
    const unmet = fields.map(unmetRequirements);
    if (unmet.some((u) => u.length === 0)) return null;
    return offCapabilities(unmet.flat());
  };

  const setValue = (key: string, value: FieldValue) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setMessage(null);
  };

  const goToCapability = (id: string) => {
    setActiveGroup(FEATURES_GROUP);
    setFocusCapability(id);
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

  if (error || !state) {
    return (
      <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg">
        Error: {error || 'Settings are unavailable.'}
      </div>
    );
  }

  const saveActions =
    dirtyCount > 0 ? (
      <div className="flex items-center gap-2 flex-shrink-0">
        <Button variant="outline" size="sm" onClick={handleDiscard} disabled={isSaving}>
          <RotateCcw className="w-4 h-4 mr-1" /> Discard
        </Button>
        <Button size="sm" onClick={handleSave} disabled={isSaving} className="bg-primary text-white">
          {isSaving ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}
          Save {dirtyCount} change{dirtyCount > 1 ? 's' : ''}
        </Button>
      </div>
    ) : null;

  const renderField = (f: SettingField) => {
    const set = (v: FieldValue) => setValue(f.key, v);
    if (f.type === 'collection') {
      return <CollectionField key={f.key} field={f} value={(valueOf(f) as CollectionRow[]) || []} onChange={set} />;
    }
    if (f.type === 'string_list') {
      return <StringListField key={f.key} field={f} value={(valueOf(f) as string[]) || []} onChange={set} />;
    }
    if (f.type === 'catalog') {
      if (f.kind === 'community_links') {
        return (
          <CommunityLinksEditor key={f.key} field={f} value={valueOf(f) as CommunityLinksCatalog} onChange={set} />
        );
      }
      if (f.kind === 'embedded_apps') {
        return (
          <EmbeddedAppsEditor key={f.key} field={f} value={(valueOf(f) as EmbeddedApp[]) || []} onChange={set} />
        );
      }
    }
    return <FieldRow key={f.key} field={f} value={valueOf(f)} onChange={set} />;
  };

  const renderGroupPage = (group: string) => {
    const fields = fieldsForGroup(group);
    const active = fields.filter((f) => unmetRequirements(f).length === 0);
    const inactive = fields.filter((f) => unmetRequirements(f).length > 0);
    const pageOff = pageOffCapabilities(group);
    const inactiveCaps = offCapabilities(inactive.flatMap(unmetRequirements));

    return (
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>{group}</CardTitle>
            {state.group_descriptions?.[group] && (
              <p className="text-sm text-gray-500 mt-1.5 max-w-2xl leading-relaxed">
                {state.group_descriptions[group]}
              </p>
            )}
          </div>
          {saveActions}
        </CardHeader>
        <CardContent className="space-y-5">
          {pageOff && <InactiveNote capabilities={pageOff} onGoTo={goToCapability} whole />}
          <SectionedFields fields={active} renderField={renderField} />
          {inactive.length > 0 && (
            <InactiveSettings
              count={inactive.length}
              note={pageOff ? null : <InactiveNote capabilities={inactiveCaps} onGoTo={goToCapability} />}
            >
              <SectionedFields fields={inactive} renderField={renderField} />
            </InactiveSettings>
          )}
          {fields.length === 0 && <p className="text-sm text-gray-500">No settings in this group.</p>}
        </CardContent>
      </Card>
    );
  };

  return (
    <div className="flex gap-6 min-h-[calc(100vh-240px)]">
      {/* Sub-nav: sections mirror the app sidebar. */}
      <div className="w-64 flex-shrink-0">
        <Card className="h-full">
          <CardContent className="p-2">
            <nav className="space-y-4 py-1">
              {state.sections.map((section) => (
                <div key={section.title}>
                  <h3 className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-gray-400">
                    {section.title}
                  </h3>
                  <div className="space-y-0.5">
                    {section.groups.map((g) => {
                      const isActive = activeGroup === g;
                      const off = pageOffCapabilities(g) !== null;
                      return (
                        <button
                          key={g}
                          onClick={() => setActiveGroup(g)}
                          className={cn(
                            'w-full text-left px-3 py-1.5 rounded-md text-sm transition-colors flex items-center gap-2',
                            isActive
                              ? 'bg-primary text-white'
                              : off
                                ? 'text-gray-400 hover:bg-gray-50'
                                : 'text-gray-700 hover:bg-gray-100',
                          )}
                        >
                          <span className="flex-1 truncate">{g}</span>
                          {off && (
                            <span
                              className={cn(
                                'text-[10px] font-semibold uppercase rounded px-1.5 py-0.5',
                                isActive ? 'bg-white/20 text-white' : 'bg-gray-100 text-gray-500',
                              )}
                            >
                              Off
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
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
        ) : activeGroup === FEATURES_GROUP ? (
          <Card>
            <CardHeader className="flex flex-row items-start justify-between gap-4">
              <div>
                <CardTitle>{FEATURES_GROUP}</CardTitle>
                {state.group_descriptions?.[FEATURES_GROUP] && (
                  <p className="text-sm text-gray-500 mt-1.5 max-w-2xl leading-relaxed">
                    {state.group_descriptions[FEATURES_GROUP]}
                  </p>
                )}
              </div>
              {saveActions}
            </CardHeader>
            <CardContent>
              <CapabilitiesPanel
                capabilities={state.capabilities}
                sectionOrder={state.capability_sections}
                switchOn={switchOn}
                onToggle={(key, v) => setValue(key, v)}
                focusId={focusCapability}
                onFocusDone={() => setFocusCapability(null)}
              />
            </CardContent>
          </Card>
        ) : (
          <>
            {renderGroupPage(activeGroup)}
            {activeGroup === INFRA_GROUP && <ReadonlyPanel fields={state.readonly || []} />}
          </>
        )}
      </div>
    </div>
  );
};

// Renders fields in spec order, with a sub-heading whenever the field
// `section` changes. Fields without a section sit above any sub-heading.
function SectionedFields({
  fields,
  renderField,
}: {
  fields: SettingField[];
  renderField: (f: SettingField) => ReactNode;
}) {
  const blocks: { section: string; fields: SettingField[] }[] = [];
  for (const f of fields) {
    const section = f.section || '';
    const existing = blocks.find((b) => b.section === section);
    if (existing) existing.fields.push(f);
    else blocks.push({ section, fields: [f] });
  }
  blocks.sort((a, b) => (a.section === '' ? -1 : b.section === '' ? 1 : 0));

  return (
    <>
      {blocks.map((b) => (
        <div key={b.section || '_'} className="space-y-5">
          {b.section && (
            <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 pt-2 border-b border-gray-200 pb-1.5">
              {b.section}
            </h4>
          )}
          {b.fields.map(renderField)}
        </div>
      ))}
    </>
  );
}

function joinLabels(labels: string[]): string {
  if (labels.length <= 1) return labels[0] || '';
  return `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`;
}

// "These apply to X, which is turned off" + a jump to each capability.
function InactiveNote({
  capabilities,
  onGoTo,
  whole = false,
}: {
  capabilities: Capability[];
  onGoTo: (id: string) => void;
  whole?: boolean;
}) {
  const labels = capabilities.map((c) => c.label);
  const subject = whole ? 'This page applies' : 'These apply';
  const verb = labels.length > 1 ? 'which are turned off' : 'which is turned off';
  const text = labels.length
    ? `${subject} to ${joinLabels(labels)}, ${verb}. Turn ${labels.length > 1 ? 'them' : 'it'} on in Features & Navigation to edit ${whole ? 'these settings' : 'them'}.`
    : `${subject} to a capability that is turned off.`;

  return (
    <div
      className={cn(
        'flex items-start gap-2.5 rounded-md text-sm p-3',
        whole ? 'bg-amber-50 border border-amber-200 text-amber-900' : 'bg-gray-50 border border-gray-200 text-gray-600',
      )}
    >
      <PowerOff className="w-4 h-4 mt-0.5 flex-shrink-0" />
      <div className="space-y-2">
        <p>{text}</p>
        {capabilities.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {capabilities.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => onGoTo(c.id)}
                className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
              >
                Turn on {c.label} <ArrowRight className="w-3 h-3" />
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// Collapsed, grayed-out, read-only list of settings whose capability is off.
function InactiveSettings({
  count,
  note,
  children,
}: {
  count: number;
  note: ReactNode;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border border-dashed border-gray-300">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-sm font-medium text-gray-500 hover:text-gray-700"
      >
        <ChevronRight className={cn('w-4 h-4 transition-transform', open && 'rotate-90')} />
        Inactive settings ({count})
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-4">
          {note}
          <fieldset disabled aria-disabled className="opacity-60 space-y-5 select-none">
            {children}
          </fieldset>
        </div>
      )}
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  disabled = false,
  size = 'md',
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  size?: 'md' | 'sm';
  label?: string;
}) {
  const sm = size === 'sm';
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative inline-flex flex-shrink-0 items-center rounded-full transition-colors disabled:cursor-not-allowed',
        sm ? 'h-5 w-9' : 'h-6 w-11',
        checked ? 'bg-primary' : 'bg-gray-300',
        disabled && 'opacity-50',
      )}
    >
      <span
        className={cn(
          'inline-block transform rounded-full bg-white transition-transform',
          sm ? 'h-3.5 w-3.5' : 'h-4 w-4',
          checked ? (sm ? 'translate-x-[18px]' : 'translate-x-6') : sm ? 'translate-x-[3px]' : 'translate-x-1',
        )}
      />
    </button>
  );
}

// The Features & Navigation page: one row per capability, grouped by the
// sidebar section it lives in. Feature flags are the main switch; the sidebar
// tabs a capability owns are nested under it and only editable while it is on.
function CapabilitiesPanel({
  capabilities,
  sectionOrder,
  switchOn,
  onToggle,
  focusId,
  onFocusDone,
}: {
  capabilities: Capability[];
  sectionOrder: string[];
  switchOn: (key: string) => boolean;
  onToggle: (key: string, value: boolean) => void;
  focusId: string | null;
  onFocusDone: () => void;
}) {
  useEffect(() => {
    if (!focusId) return;
    document.getElementById(`capability-${focusId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const t = window.setTimeout(onFocusDone, 2500);
    return () => window.clearTimeout(t);
  }, [focusId, onFocusDone]);

  const sections = sectionOrder
    .map((title) => ({ title, caps: capabilities.filter((c) => c.section === title) }))
    .filter((s) => s.caps.length > 0);

  return (
    <div className="space-y-8">
      {sections.map((section) => {
        const groups: { name: string; caps: Capability[] }[] = [];
        for (const c of section.caps) {
          const g = groups.find((x) => x.name === c.group);
          if (g) g.caps.push(c);
          else groups.push({ name: c.group, caps: [c] });
        }
        return (
          <div key={section.title} className="space-y-3">
            <h3 className="text-sm font-semibold text-gray-900 border-b border-gray-200 pb-1.5">{section.title}</h3>
            {section.title === 'Other' && (
              <p className="text-xs text-gray-500">
                Flags and sidebar switches that aren't part of a capability above. New ones appear here automatically.
              </p>
            )}
            {groups.map((g) => (
              <div key={g.name || '_'} className="space-y-2">
                {g.name && (
                  <h4 className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 pt-1">{g.name}</h4>
                )}
                <div className="divide-y divide-gray-100 rounded-md border border-gray-200">
                  {g.caps.map((c) => (
                    <CapabilityRow
                      key={c.id}
                      capability={c}
                      switchOn={switchOn}
                      onToggle={onToggle}
                      highlighted={focusId === c.id}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function CapabilityRow({
  capability: c,
  switchOn,
  onToggle,
  highlighted,
}: {
  capability: Capability;
  switchOn: (key: string) => boolean;
  onToggle: (key: string, value: boolean) => void;
  highlighted: boolean;
}) {
  const on = switchOn(c.primary);
  const sidebarOnly = c.primary.startsWith('ui.tabs.');
  return (
    <div
      id={`capability-${c.id}`}
      className={cn('p-3 transition-colors', highlighted && 'bg-primary/5 ring-2 ring-primary/40 rounded-md')}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-gray-800">{c.label}</span>
            {sidebarOnly && (
              <span className="text-[10px] font-medium uppercase tracking-wide rounded bg-gray-100 text-gray-500 px-1.5 py-0.5">
                Sidebar only
              </span>
            )}
          </div>
          {c.description && <p className="text-xs text-gray-500 max-w-2xl">{c.description}</p>}
          {c.warning && (
            <p className="flex items-start gap-1.5 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1.5 max-w-2xl">
              <AlertTriangle className="w-3.5 h-3.5 mt-px flex-shrink-0" />
              {c.warning}
            </p>
          )}
        </div>
        <Toggle checked={on} onChange={(v) => onToggle(c.primary, v)} label={c.label} />
      </div>
      {c.tabs.length > 0 && (
        <div className="mt-2.5 ml-4 pl-3 border-l-2 border-gray-100 space-y-2">
          {c.tabs.map((t) => {
            const tabOn = switchOn(t.key);
            return (
              <div key={t.key} className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <span className={cn('text-xs font-medium', on ? 'text-gray-700' : 'text-gray-400')}>{t.label}</span>
                  {!on ? (
                    <p className="text-[11px] text-gray-400">Available when {c.label} is on.</p>
                  ) : (
                    t.help && <p className="text-[11px] text-gray-500">{t.help}</p>
                  )}
                </div>
                <Toggle
                  size="sm"
                  checked={on && tabOn}
                  disabled={!on}
                  onChange={(v) => onToggle(t.key, v)}
                  label={`${c.label}: ${t.label}`}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Live, plain-English translation of a 5-field cron expression, so an admin
// doesn't have to parse a raw expression in their head. Blank = disabled; an
// unparseable value is flagged inline. Always offers a link to crontab.guru for
// building/verifying expressions.
function CronHint({ value }: { value: string }) {
  const trimmed = value.trim();

  // Resolve to a plain string + tone first; building JSX inside the try/catch
  // trips react-hooks/error-boundaries (render errors aren't caught there).
  let text: string;
  let tone: 'muted' | 'normal' | 'error';
  if (!trimmed) {
    text = 'Disabled — no scheduled runs.';
    tone = 'muted';
  } else {
    try {
      text = `${cronstrue.toString(trimmed, { throwExceptionOnParseError: true })} (UTC)`;
      tone = 'normal';
    } catch {
      text = 'Not a valid 5-field cron expression.';
      tone = 'error';
    }
  }

  const toneClass =
    tone === 'error'
      ? 'text-red-500'
      : tone === 'muted'
        ? 'text-gray-400 italic'
        : 'text-gray-600';

  return (
    <p className="text-xs flex items-center gap-1.5 flex-wrap">
      <span className={toneClass}>{text}</span>
      <span className="text-gray-300">·</span>
      <a
        href="https://crontab.guru/"
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-0.5 text-blue-600 hover:underline"
      >
        build/verify a cron
        <ExternalLink className="w-3 h-3" />
      </a>
    </p>
  );
}

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
        <Toggle checked={Boolean(value)} onChange={onChange} label={field.label} />
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
      ) : field.type === 'password' ? (
        <input
          type="password"
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
          placeholder="••••••••"
          autoComplete="new-password"
          className="w-full max-w-xl px-3 py-2 border rounded-md text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/20 border-gray-200"
        />
      ) : field.type === 'cron' ? (
        <div className="space-y-1.5">
          <input
            type="text"
            value={String(value ?? '')}
            onChange={(e) => onChange(e.target.value)}
            placeholder="*/30 * * * *  (blank = disabled)"
            spellCheck={false}
            className="w-64 px-3 py-2 border rounded-md text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/20 border-gray-200"
          />
          <CronHint value={String(value ?? '')} />
        </div>
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
                      ) : c.type === 'select' ? (
                        <select
                          value={row[c.key] === null || row[c.key] === undefined ? '' : String(row[c.key])}
                          onChange={(e) => updateCell(idx, c.key, e.target.value)}
                          className="w-full min-w-[8rem] px-2 py-1.5 border rounded-md text-sm bg-white focus:outline-none focus:ring-2 focus:ring-primary/20 border-gray-200"
                        >
                          {(c.options || []).map((opt) => (
                            <option key={opt} value={opt}>{opt === '' ? (c.placeholder || '—') : opt}</option>
                          ))}
                        </select>
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
          The settings below are managed in <code className="font-mono">databricks.yml</code> and secrets. They are
          shown for reference and take effect only on redeploy/restart.
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
