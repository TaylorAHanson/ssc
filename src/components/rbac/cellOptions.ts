import type { RbacField, RbacSheetInfo, RbacSheetOptions } from '../../types/rbac';
import type { GridRow } from './staging';
import { PACKED_FIELDS, splitPacked } from './utils';

export type Normalize = (value: string) => string;

// The backend's comparison keys (layout, policy, cells), so a value it accepts is never shown as outside the options.
const nameKey: Normalize = (v) => v.trim().toLowerCase();
const securableKey: Normalize = (v) => v.trim().toLowerCase().replace(/[\s_]+/g, ' ');
const privilegeKey: Normalize = (v) => v.trim().replace(/\s+/g, '_').toUpperCase();

function principalTypeKey(value: string): string {
  const cleaned = value.trim().toLowerCase().replace(/[\s_-]+/g, ' ');
  const compact = cleaned.replace(/ /g, '');
  if (compact === 'group' || compact === 'groups') return 'group';
  if (['serviceprincipal', 'serviceprincipals', 'sp', 'spn'].includes(compact)) return 'service principal';
  if (compact === 'user' || compact === 'users') return 'user';
  return cleaned;
}

const WORKSPACE_SECURABLES = new Set(['workspace', 'workspace entitlement']);

/** How a cell is edited: a choice from the sheet's options, or free text with suggestions from the sheet. */
export type EditorSpec =
  | { kind: 'select'; options: string[]; match: Normalize }
  | { kind: 'multi'; options: string[]; match: Normalize; heading: string; empty: string }
  | { kind: 'text'; packed: boolean; suggestions: string[] };

/** A cell value (or packed atoms) outside the options, on a row the user added or changed. */
export interface CellFlag {
  values: ReadonlySet<string>;
  reason: string;
}

export const optionDomId = (listId: string, index: number) => `${listId}-${index}`;

export function dedupe(values: string[], match: Normalize): string[] {
  const seen = new Set<string>();
  return values.filter((v) => {
    const key = match(v);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** The row's environment key, resolved the way the backend does for the rows it has seen. */
export function rowEnvironment(sheet: RbacSheetInfo, row: GridRow): string | null {
  if (sheet.environment) return sheet.environment;
  const value = nameKey(row.cells.environment_value ?? '');
  const key = (sheet.options?.environments ?? []).find((e) => nameKey(e) === value);
  if (key) return key;
  // An alias the backend resolved on load; it no longer applies once the cell is edited.
  if (row.rule && !row.changed.has('environment_value')) return row.rule.environment;
  return null;
}

function principalTypesFor(options: Partial<RbacSheetOptions>, env: string | null): string[] {
  const byEnv = options.principal_types ?? {};
  if (env && byEnv[env]) return byEnv[env];
  return dedupe(Object.values(byEnv).flat(), principalTypeKey);
}

/** Undefined when no allow list was sent; otherwise what it permits on the type (possibly nothing). */
function privilegesFor(options: Partial<RbacSheetOptions> | undefined, securableType: string): string[] | undefined {
  const map = options?.privileges;
  if (!map || Object.keys(map).length === 0) return undefined;
  const key = securableKey(securableType);
  return Object.entries(map).find(([type]) => securableKey(type) === key)?.[1] ?? [];
}

function environmentChoices(sheet: RbacSheetInfo): string[] {
  const envs = sheet.options?.environments ?? [];
  if (envs.length) return envs;
  return sheet.environment ? [sheet.environment] : [];
}

/** Distinct non-empty values of a column (each atom, for packed cells), alphabetically. */
export function distinctValues(rows: GridRow[], field: RbacField): string[] {
  const packed = PACKED_FIELDS.has(field);
  const byKey = new Map<string, string>();
  for (const row of rows) {
    const value = row.cells[field] ?? '';
    for (const v of packed ? splitPacked(value) : [value.trim()]) {
      const key = v.toLowerCase();
      if (v && !byKey.has(key)) byKey.set(key, v);
    }
  }
  return [...byKey.values()].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
}

export function editorFor(sheet: RbacSheetInfo, row: GridRow, field: RbacField, rows: GridRow[]): EditorSpec {
  const options = sheet.options;
  const type = row.cells.securable_type ?? '';
  const choose = (values: string[] | undefined, match: Normalize): EditorSpec | null =>
    values && values.length ? { kind: 'select', options: values, match } : null;

  let spec: EditorSpec | null = null;
  switch (field) {
    case 'environment_value':
      spec = choose(environmentChoices(sheet), nameKey);
      break;
    case 'securable_type':
      spec = choose(options?.securable_types, securableKey);
      break;
    case 'principal_type':
      spec = options ? choose(principalTypesFor(options, rowEnvironment(sheet, row)), principalTypeKey) : null;
      break;
    case 'privilege': {
      const allowed = privilegesFor(options, type);
      if (allowed) {
        spec = {
          kind: 'multi',
          options: allowed,
          match: privilegeKey,
          heading: type.trim() ? `Allowed on ${type.trim()}` : 'Allowed privileges',
          empty: type.trim()
            ? `The allow list permits nothing on ${type.trim()}.`
            : 'Choose the securable type first to see its privileges.',
        };
      }
      break;
    }
    case 'workspace_name':
      if (sheet.plane === 'workspace') spec = choose(options?.workspaces, nameKey);
      break;
    case 'object_name':
      if (sheet.plane === 'workspace' && WORKSPACE_SECURABLES.has(securableKey(type))) spec = choose(options?.workspaces, nameKey);
      break;
    case 'catalog':
      if (sheet.plane === 'uc') spec = choose(options?.catalogs, nameKey);
      break;
  }
  return spec ?? { kind: 'text', packed: PACKED_FIELDS.has(field), suggestions: distinctValues(rows, field) };
}

/**
 * Values the user's change puts outside the options. Untouched imported values aren't flagged,
 * matching the policy, which judges only what a change adds.
 */
export function flagFor(sheet: RbacSheetInfo, row: GridRow, field: RbacField): CellFlag | null {
  const options = sheet.options;
  if (!options || row.state === 'deleted' || row.state === 'clean') return null;
  const touched = (f: RbacField) => row.state === 'new' || row.changed.has(f);
  const value = (row.cells[field] ?? '').trim();
  const outside = (allowed: string[], match: Normalize) =>
    value !== '' && allowed.length > 0 && !allowed.some((a) => match(a) === match(value));

  if (field === 'securable_type') {
    if (!touched(field) || !outside(options.securable_types ?? [], securableKey)) return null;
    return { values: new Set([row.cells[field] ?? '']), reason: 'Not a securable type this sheet offers' };
  }
  if (field === 'principal_type') {
    if (!touched(field) && !touched('environment_value')) return null;
    const env = rowEnvironment(sheet, row);
    if (!outside(principalTypesFor(options, env), principalTypeKey)) return null;
    return { values: new Set([row.cells[field] ?? '']), reason: env ? `Not allowed in ${env}` : 'Not an allowed principal type' };
  }
  if (field === 'privilege') {
    const type = (row.cells.securable_type ?? '').trim();
    const allowed = type ? privilegesFor(options, type) : undefined;
    if (!allowed) return null;
    const keys = new Set(allowed.map(privilegeKey));
    const kept = touched('securable_type')
      ? new Set<string>()
      : new Set(splitPacked(row.original?.cells.privilege).map(privilegeKey));
    const added = splitPacked(row.cells.privilege).filter((a) => !keys.has(privilegeKey(a)) && !kept.has(privilegeKey(a)));
    return added.length ? { values: new Set(added), reason: `Not on the allow list for ${type}` } : null;
  }
  const workspaceNamed =
    sheet.plane === 'workspace' &&
    ((field === 'workspace_name' && touched(field)) ||
      (field === 'object_name' &&
        WORKSPACE_SECURABLES.has(securableKey(row.cells.securable_type ?? '')) &&
        (touched(field) || touched('securable_type'))));
  if (workspaceNamed && outside(options.workspaces ?? [], nameKey)) {
    return { values: new Set([row.cells[field] ?? '']), reason: 'Not one of the workspaces set up for this environment' };
  }
  if (field === 'catalog' && sheet.plane === 'uc' && touched(field) && outside(options.catalogs ?? [], nameKey)) {
    return { values: new Set([row.cells[field] ?? '']), reason: 'Not one of the catalogs set up for this environment' };
  }
  return null;
}

/** A single-choice list: the options, with a current value that isn't one of them kept first. */
export function selectChoices(current: string, options: string[], match: Normalize): { values: string[]; selected: number; outside: boolean } {
  const values = dedupe(options, match);
  if (!current.trim()) return { values, selected: -1, outside: false };
  const index = values.findIndex((v) => match(v) === match(current));
  if (index >= 0) return { values, selected: index, outside: false };
  return { values: [current, ...values], selected: 0, outside: true };
}

/** A multi-choice list: atoms already in the cell that aren't options first, then the options. */
export function multiChoices(current: string, options: string[], match: Normalize): { value: string; outside: boolean }[] {
  const allowed = dedupe(options, match);
  const keys = new Set(allowed.map(match));
  const outside = dedupe(splitPacked(current).filter((a) => !keys.has(match(a))), match);
  return [...outside.map((value) => ({ value, outside: true })), ...allowed.map((value) => ({ value, outside: false }))];
}

/** The packed cell for a selection: kept atoms stay in place, added ones follow in option order. */
export function packSelection(current: string, chosen: ReadonlySet<string>, options: string[], match: Normalize): string {
  const atoms = splitPacked(current);
  const kept = atoms.filter((a) => chosen.has(match(a)));
  const had = new Set(atoms.map(match));
  const added = dedupe(options, match).filter((o) => chosen.has(match(o)) && !had.has(match(o)));
  if (kept.length === atoms.length && added.length === 0) return current;
  return [...kept, ...added].join(';');
}

const SUGGESTION_LIMIT = 50;

/**
 * Suggestions for a text cell. Until the user types, every value is offered; after that, the
 * ones containing what was typed (the last atom, in a packed cell), prefix matches first.
 */
export function suggest(values: string[], draft: string, packed: boolean, typed: boolean): { shown: string[]; more: number } {
  const token = typed ? (packed ? draft.slice(draft.lastIndexOf(';') + 1) : draft).trim().toLowerCase() : '';
  const taken = new Set((packed ? splitPacked(draft) : [draft.trim()]).map((v) => v.toLowerCase()));
  const hits = values.filter((v) => {
    const key = v.toLowerCase();
    return !taken.has(key) && key.includes(token);
  });
  if (token) hits.sort((a, b) => Number(!a.toLowerCase().startsWith(token)) - Number(!b.toLowerCase().startsWith(token)));
  return { shown: hits.slice(0, SUGGESTION_LIMIT), more: Math.max(0, hits.length - SUGGESTION_LIMIT) };
}

/** The draft with a suggestion applied: it replaces what was typed, or joins a packed cell as a new atom. */
export function applySuggestion(draft: string, value: string, packed: boolean, typed: boolean): string {
  if (!packed) return value;
  if (typed) return draft.slice(0, draft.lastIndexOf(';') + 1) + value;
  const head = draft.replace(/[;\s]+$/, '');
  return head ? `${head};${value}` : value;
}
