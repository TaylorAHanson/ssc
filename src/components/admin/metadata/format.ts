import { format, formatDistanceToNow, parseISO } from 'date-fns';
import type {
  ActorType,
  ApprovalRouting,
  ChangeEvent,
  ChangeItem,
  ChangeOpType,
  ChangeSet,
  ChangeSource,
  DriftEntry,
  ItemDecision,
  KeyInfo,
  MetadataConfig,
  ObjectSummary,
  SecurableType,
  Tags,
} from '../../../types/metadata';

// Naive UTC datetimes (no zone suffix) are read as UTC.
export const parseUtc = (value: string): Date => parseISO(/Z|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`);

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—';
  const d = parseUtc(value);
  return Number.isNaN(d.getTime()) ? value : format(d, 'MMM d, yyyy HH:mm');
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return '';
  const d = parseUtc(value);
  return Number.isNaN(d.getTime()) ? '' : formatDistanceToNow(d, { addSuffix: true });
}

export const plural = (n: number, one: string, many = `${one}s`): string => `${n.toLocaleString()} ${n === 1 ? one : many}`;

export function errorMessage(e: unknown, fallback = 'Something went wrong'): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

export const TYPE_LABELS: Record<SecurableType, string> = {
  catalog: 'Catalog',
  schema: 'Schema',
  table: 'Table',
  view: 'View',
  materialized_view: 'Materialized view',
  streaming_table: 'Streaming table',
  metric_view: 'Metric view',
  column: 'Column',
};

export const SOURCE_LABELS: Record<ChangeSource, string> = {
  ui: 'Metadata Manager',
  api: 'API',
  mcp: 'MCP',
  agent: 'Assistant',
  adopt: 'Adopted',
  import: 'Import',
  system: 'System',
  revert: 'Revert',
};

export const sourceLabel = (source: string | null | undefined): string =>
  source ? SOURCE_LABELS[source as ChangeSource] ?? source.replace(/_/g, ' ') : '—';

export const ACTOR_TYPE_LABELS: Record<ActorType, string> = {
  user: 'Person',
  service_principal: 'Service principal',
  system: 'System',
};

export const OPERATION_LABELS: Record<ChangeOpType, string> = {
  set_tags: 'Add or change tags',
  unset_tags: 'Remove tags',
  replace_tags: 'Replace all tags',
  set_comment: 'Set descriptions',
  clear_comment: 'Clear descriptions',
};

export const ROUTING_OPTIONS: { value: ApprovalRouting; label: string; hint: string }[] = [
  {
    value: 'request',
    label: 'Send the whole change for approval',
    hint: 'If any edit needs approval, nothing is saved until the approvers agree; then everything is saved together.',
  },
  {
    value: 'split',
    label: 'Save what I can now, send the rest for approval',
    hint: 'Edits you may make yourself are saved straight away; the others go to their approvers.',
  },
  {
    value: 'reject',
    label: 'Don’t save anything if approval is needed',
    hint: 'Useful for checking: if any edit needs approval, nothing is saved or sent.',
  },
];

/** `pii` for a key-only tag, `classification=restricted` otherwise. */
export const tagText = (key: string, value: string | null | undefined): string =>
  value === null || value === undefined || value === '' ? key : `${key}=${value}`;

export const objectLabel = (name: string, column?: string | null): string => (column ? `${name}.${column}` : name);

export function fqnKey(name: string, column?: string | null): string {
  const base = name.trim().toLowerCase();
  return column ? `${base}::${column.trim().toLowerCase()}` : base;
}

/** Tags as shown for a row: desired tags when managed, else what Unity Catalog reports. */
export const displayTags = (s: ObjectSummary): Tags => (s.managed ? s.tags : s.observed_tags ?? s.tags ?? {});

// --- Tag filters ---------------------------------------------------------------------

/** `key=value`, `key` (has it) or `!key` (lacks it). */
export interface TagFilterChip {
  raw: string;
  op: 'eq' | 'exists' | 'missing';
  key: string;
  value: string | null;
}

export function parseTagFilter(text: string): TagFilterChip | null {
  const raw = text.trim();
  if (!raw) return null;
  if (raw.startsWith('!')) {
    const key = raw.slice(1).trim();
    return key ? { raw: `!${key}`, op: 'missing', key, value: null } : null;
  }
  const eq = raw.indexOf('=');
  if (eq < 0) return { raw, op: 'exists', key: raw, value: null };
  const key = raw.slice(0, eq).trim();
  const value = raw.slice(eq + 1).trim();
  return key ? { raw: `${key}=${value}`, op: 'eq', key, value: value || null } : null;
}

export function describeTagFilter(chip: TagFilterChip): string {
  if (chip.op === 'missing') return `without ${chip.key}`;
  if (chip.op === 'exists') return `has ${chip.key}`;
  return chip.value === null ? `${chip.key} (key only)` : `${chip.key} = ${chip.value}`;
}

const isTagToken = (w: string) => w.includes('=') || (w.startsWith('!') && w.length > 1);

/** Splits a search string into free text and tag filter tokens (`a=b`, `!a`; `a=*` means "has a"). */
export function splitSearch(text: string): { q: string; tags: string[] } {
  const words = text.trim().split(/\s+/).filter(Boolean);
  const tags = words
    .filter(isTagToken)
    .map((w) => parseTagFilter(w.endsWith('=*') ? w.slice(0, -2) : w)?.raw)
    .filter((t): t is string => !!t);
  const q = words.filter((w) => !isTagToken(w)).join(' ');
  return { q, tags };
}

// --- Drift ---------------------------------------------------------------------------

/** Desired vs observed for a search result, for lists where only the summary is loaded. */
export function summaryDrift(s: ObjectSummary, exemptKeys: string[]): DriftEntry[] {
  if (!s.managed || !s.observed_tags) return [];
  const out: DriftEntry[] = [];
  const desired = s.tags ?? {};
  const observed = s.observed_tags ?? {};
  const norm = (v: string | null | undefined) => (v === '' || v === undefined ? null : v);
  for (const [k, v] of Object.entries(desired)) {
    if (!(k in observed)) out.push({ field: 'tag', key: k, desired: v, observed: null, kind: 'missing', exempt: exemptKeys.includes(k) });
    else if (norm(observed[k]) !== norm(v)) out.push({ field: 'tag', key: k, desired: v, observed: observed[k], kind: 'changed', exempt: exemptKeys.includes(k) });
  }
  for (const [k, v] of Object.entries(observed)) {
    if (!(k in desired)) out.push({ field: 'tag', key: k, desired: null, observed: v, kind: 'extra', exempt: exemptKeys.includes(k) });
  }
  const dc = s.comment ?? null;
  const oc = s.observed_comment ?? null;
  if (dc !== oc) out.push({ field: 'comment', key: null, desired: dc, observed: oc, kind: dc === null ? 'extra' : oc === null ? 'missing' : 'changed', exempt: false });
  return out;
}

export const DRIFT_KIND_LABELS: Record<DriftEntry['kind'], string> = {
  missing: 'Missing in Unity Catalog',
  extra: 'Only in Unity Catalog',
  changed: 'Different value',
};

// --- Change events and sets ----------------------------------------------------------

const ACTION_LABELS: Record<string, string> = {
  tag_set: 'Set tag',
  tag_unset: 'Removed tag',
  comment_set: 'Set description',
  comment_clear: 'Cleared description',
  object_create: 'Prepared ahead of time',
  object_delete: 'Deleted object',
  object_restore: 'Restored object',
  object_state: 'Object state changed',
};

export const actionLabel = (action: string): string => ACTION_LABELS[action] ?? action.replace(/_/g, ' ');

/** A before/after snapshot as text: `{value, version}` for tags and descriptions. */
export function eventValue(snapshot: Record<string, unknown> | null | undefined): { text: string; kind: 'none' | 'keyonly' | 'value' } {
  if (!snapshot) return { text: 'none', kind: 'none' };
  if ('value' in snapshot) {
    const v = snapshot.value;
    return v === null || v === undefined || v === '' ? { text: 'key only', kind: 'keyonly' } : { text: String(v), kind: 'value' };
  }
  const entries = Object.entries(snapshot);
  if (!entries.length) return { text: 'none', kind: 'none' };
  return { text: entries.map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(', '), kind: 'value' };
}

const SUMMARY_LABELS: Record<string, string> = {
  objects: 'objects',
  tags_set: 'tags set',
  tags_unset: 'tags removed',
  comments: 'descriptions changed',
  created: 'objects prepared ahead of time',
  restored: 'objects restored',
};

/** Readable counts from a change set's summary (falls back to its events). */
export function changeSetCounts(cs: ChangeSet): string[] {
  const parts = Object.entries(cs.summary ?? {})
    .filter(([, v]) => typeof v === 'number' && v > 0)
    .map(([k, v]) => `${(v as number).toLocaleString()} ${SUMMARY_LABELS[k] ?? k.replace(/_/g, ' ')}`);
  if (parts.length) return parts;
  return cs.events.length ? [plural(cs.events.length, 'change')] : [];
}

export const eventsByObject = (events: ChangeEvent[]): [string, ChangeEvent[]][] => {
  const map = new Map<string, ChangeEvent[]>();
  events.forEach((e) => map.set(e.fqn_key, [...(map.get(e.fqn_key) ?? []), e]));
  return [...map.entries()];
};

// --- Preview decisions ---------------------------------------------------------------

export function describeItem(item: ChangeItem): string {
  const v = (s: string | null | undefined) => (s === null || s === undefined || s === '' ? '(key only)' : s);
  if (item.field === 'comment') {
    if (item.action === 'comment_clear') return 'Clear the description';
    return item.before_exists && item.before ? `Change the description to “${item.after ?? ''}”` : `Set the description to “${item.after ?? ''}”`;
  }
  if (item.action === 'unset') return `Remove ${item.key}${item.before_exists ? ` (was ${v(item.before)})` : ''}`;
  if (item.before_exists) return `${item.key}: ${v(item.before)} → ${v(item.after)}`;
  return `Add ${item.key}${item.after ? `=${item.after}` : ' (key only)'}`;
}

export type Outcome = 'direct' | 'approval' | 'denied';

export function outcomeOf(d: ItemDecision): Outcome {
  if (d.findings.some((f) => f.severity === 'deny')) return 'denied';
  if (d.decision.allowed && !d.decision.needs_approval) return 'direct';
  if (d.decision.needs_approval || d.decision.can_request) return 'approval';
  return 'denied';
}

export const VIA_LABELS: Record<string, string> = {
  admin: 'as an admin',
  owner: 'as an owner',
  uc_owner: 'as the Unity Catalog owner',
  principal: 'within the principal’s scope',
  none: '',
};

export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** Client-side checks that mirror the key policies; the server re-checks on preview. */
export function tagProblem(key: string, value: string | null, config: MetadataConfig, keys: KeyInfo[]): string | null {
  if (!key) return 'Enter a tag key.';
  if (/\s/.test(key)) return 'Tag keys can’t contain spaces.';
  if (config.exempt_keys.includes(key)) return `${key} is managed outside this tool and can’t be edited here.`;
  if (config.key_mode === 'known_only' && !keys.some((k) => k.key === key) && !config.key_policies.some((p) => p.key === key)) {
    return `${key} isn’t a known key. Ask an admin to add a policy for it first.`;
  }
  const policy = config.key_policies.find((p) => p.key === key);
  if (policy?.allowed_values.length && (value === null || !policy.allowed_values.includes(value))) {
    return `${key} must be one of: ${policy.allowed_values.join(', ')}.`;
  }
  if (policy?.pattern && value !== null && !new RegExp(policy.pattern).test(value)) return `${key} must match ${policy.pattern}.`;
  return null;
}

const hasControlChars = (s: string): boolean => [...s].some((ch) => ch.charCodeAt(0) < 32 || ch.charCodeAt(0) === 127);

/** Mirrors the backend's ObjectKey.parse checks; returns a message or null. */
export function nameProblem(name: string, column?: string | null): string | null {
  const parts = name.trim().split('.');
  if (!name.trim() || parts.length > 3 || parts.some((p) => !p.trim())) return 'Use catalog, catalog.schema or catalog.schema.table.';
  if (parts.some((p) => /[`'";\s]/.test(p.trim()) || hasControlChars(p))) return 'Names can’t contain quotes, semicolons or spaces.';
  if (column?.trim() && parts.length !== 3) return 'A column needs a catalog.schema.table name.';
  if (column && (column.includes('`') || hasControlChars(column))) return 'Column names can’t contain backticks.';
  return null;
}

/** The environment a catalog belongs to, from the configured `{base}_dev`-style patterns. */
export function envForCatalog(config: MetadataConfig, catalog: string): string | null {
  const c = catalog.toLowerCase();
  for (const env of config.environments) {
    if (env.catalogs.map((x) => x.toLowerCase()).includes(c)) return env.key;
    if (!env.catalog_pattern) continue;
    const parts = env.catalog_pattern.toLowerCase().split('{base}').map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    if (new RegExp(`^${parts.join('.+')}$`).test(c)) return env.key;
  }
  return null;
}

export function inferType(name: string, column?: string | null): SecurableType {
  if (column?.trim()) return 'column';
  const parts = name.trim().split('.').filter(Boolean).length;
  return parts === 1 ? 'catalog' : parts === 2 ? 'schema' : 'table';
}
