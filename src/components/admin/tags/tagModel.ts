import { createContext } from 'react';

// Working-set model for the Metadata Manager editor: every catalog, schema,
// table, view or column the admin is editing, its tags (and, for catalogs and
// schemas, its description) as loaded from Unity Catalog, and their edits.

export interface TagRow {
  key: string;
  value: string;
}

export interface EditTarget {
  /** Lower-cased object name (`catalog`, `catalog.schema`, `catalog.schema.table`), plus `::column` for a column. */
  key: string;
  /** The object's name: a catalog, schema, table or view. */
  table: string;
  column?: string;
  objectType?: string | null;
  exists: boolean;
  original: Record<string, string>;
  rows: TagRow[];
  /** Catalogs and schemas only: the description as loaded, and as edited. */
  originalComment?: string;
  comment?: string;
}

export interface TargetDiff {
  desired: Record<string, string>;
  set: Record<string, string>;
  unset: string[];
  commentChanged: boolean;
  changed: boolean;
}

/** Catalog or schema (they hold tables and can have a description), by the object's name. */
export function isContainerName(name: string): boolean {
  return name.split('.').length < 3;
}

export function isContainer(t: { table: string; column?: string | null }): boolean {
  return !t.column && isContainerName(t.table);
}

export function targetKey(table: string, column?: string | null): string {
  const base = table.trim().toLowerCase();
  return column ? `${base}::${column.trim().toLowerCase()}` : base;
}

export function targetLabel(t: { table: string; column?: string | null }): string {
  return t.column ? `${t.table}.${t.column}` : t.table;
}

export function toTagMap(tags: Record<string, string | null>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(tags)) out[k] = v ?? '';
  return out;
}

export function makeTarget(
  table: string,
  tags: Record<string, string | null>,
  opts: { column?: string; objectType?: string | null; exists?: boolean; comment?: string | null } = {}
): EditTarget {
  const original = toTagMap(tags);
  const target: EditTarget = {
    key: targetKey(table, opts.column),
    table,
    column: opts.column,
    objectType: opts.objectType,
    exists: opts.exists ?? true,
    original,
    rows: Object.entries(original).map(([key, value]) => ({ key, value })),
  };
  if (isContainer(target)) {
    target.originalComment = opts.comment ?? '';
    target.comment = target.originalComment;
  }
  return target;
}

/** The target with its edits thrown away. */
export function resetTarget(t: EditTarget): EditTarget {
  return {
    ...t,
    rows: Object.entries(t.original).map(([key, value]) => ({ key, value })),
    comment: t.originalComment,
  };
}

export function buildDesired(rows: TagRow[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const r of rows) {
    const k = r.key.trim();
    if (k) out[k] = r.value;
  }
  return out;
}

export function diffTarget(t: EditTarget): TargetDiff {
  const desired = buildDesired(t.rows);
  const set: Record<string, string> = {};
  const unset: string[] = [];
  for (const k of Object.keys(desired)) if (t.original[k] !== desired[k]) set[k] = desired[k];
  for (const k of Object.keys(t.original)) if (!(k in desired)) unset.push(k);
  const commentChanged = t.comment !== undefined && t.comment !== (t.originalComment ?? '');
  return {
    desired,
    set,
    unset,
    commentChanged,
    changed: Object.keys(set).length > 0 || unset.length > 0 || commentChanged,
  };
}

export type RenameOutcome = 'renamed' | 'conflict' | 'absent';

/**
 * Rename tag key `from` to `to`, keeping its value and position. If the target
 * already has `to` with a different value it's a conflict and nothing changes;
 * with the same value, `from` is just dropped.
 */
export function renameKeyInRows(rows: TagRow[], from: string, to: string): { rows: TagRow[]; outcome: RenameOutcome } {
  const idx = rows.findIndex((r) => r.key.trim() === from);
  if (idx < 0 || from === to) return { rows, outcome: 'absent' };
  const existing = rows.find((r) => r.key.trim() === to);
  if (existing && existing.value !== rows[idx].value) return { rows, outcome: 'conflict' };
  const next = rows
    .map((r, i) => (i === idx ? { key: to, value: r.value } : r))
    .filter((r, i) => i === idx || r.key.trim() !== to);
  return { rows: next, outcome: 'renamed' };
}

/** Set (or overwrite) one tag on a target's rows. */
export function withTag(rows: TagRow[], key: string, value: string): TagRow[] {
  const next = [...rows];
  const idx = next.findIndex((r) => r.key.trim() === key);
  if (idx >= 0) next[idx] = { key, value };
  else next.push({ key, value });
  return next;
}

export function withoutTag(rows: TagRow[], key: string): TagRow[] {
  return rows.filter((r) => r.key.trim() !== key);
}

const TYPE_LABELS: Record<string, string> = {
  CATALOG: 'Catalog',
  SCHEMA: 'Schema',
  MANAGED: 'Table',
  EXTERNAL: 'Table',
  FOREIGN: 'Foreign table',
  TABLE: 'Table',
  'BASE TABLE': 'Table',
  VIEW: 'View',
  METRIC_VIEW: 'Metric view',
  MATERIALIZED_VIEW: 'Materialized view',
  STREAMING_TABLE: 'Streaming table',
};

export function typeLabel(objectType?: string | null): string {
  if (!objectType) return 'Table';
  return TYPE_LABELS[objectType.toUpperCase()] ?? objectType.replace(/_/g, ' ').toLowerCase();
}

/**
 * Does a target match the working-set filter? Words match the name; `key=value`
 * (with `*` wildcards) and `!key` match the target's current (edited) tags.
 */
export function matchesFilter(t: EditTarget, filter: string): boolean {
  const tokens = filter.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return true;
  const tags = buildDesired(t.rows);
  const lowered: Record<string, string> = {};
  for (const [k, v] of Object.entries(tags)) lowered[k.toLowerCase()] = v.toLowerCase();
  const name = targetLabel(t).toLowerCase();
  return tokens.every((tok) => {
    if (tok.startsWith('!') && tok.length > 1 && !tok.includes('=')) return !(tok.slice(1) in lowered);
    if (tok.includes('=')) {
      const [k, v] = tok.split('=', 2);
      if (!(k in lowered)) return false;
      if (!v || v === '*') return true;
      const re = new RegExp(`^${v.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*')}$`);
      return re.test(lowered[k]);
    }
    return name.includes(tok);
  });
}

/** Pull a readable message out of an API error ("...: 400 {"detail": "..."}"). */
export function errorText(e: unknown, fallback: string): string {
  if (!(e instanceof Error)) return fallback;
  const match = e.message.match(/\{.*\}$/s);
  if (match) {
    try {
      const detail = JSON.parse(match[0])?.detail;
      if (typeof detail === 'string') return detail;
    } catch {
      // not JSON — fall through to the raw message
    }
  }
  return e.message || fallback;
}

/** Built-in governance keys offered in every key field (provided by the page). */
export const SuggestedKeysContext = createContext<string[]>([]);

/** What a pending edit does to a table's dataset membership, if anything. */
export function datasetMove(t: EditTarget): { from?: string; to?: string } | null {
  if (t.column || isContainer(t)) return null;
  const desired = buildDesired(t.rows);
  for (const k of ['dataset', 'data_set']) {
    const from = t.original[k];
    const to = desired[k];
    if (from !== to && (from || to)) return { from: from || undefined, to: to || undefined };
  }
  return null;
}

/** Tags OmniGuard owns and that can't be edited here; other system.* tags can be. */
const RESERVED_KEYS = ['system.certification_status'];

export function isReservedKey(key: string): boolean {
  return RESERVED_KEYS.includes(key.trim().toLowerCase());
}

export const RESERVED_KEY_HINT =
  'Reserved: OmniGuard sets this when a dataset is certified and removes it when certification lapses.';
