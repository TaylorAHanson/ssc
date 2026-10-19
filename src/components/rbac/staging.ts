import type { RbacCells, RbacField, RbacRowChange, RbacRule, RbacSheetInfo } from '../../types/rbac';
import { ruleCells, type CellSource } from './utils';

/**
 * A change staged in the grid, not yet committed. The list order is the order sent
 * to the backend, so a finding's `change_index` points straight at its entry.
 * Updates and deletes keep the rule as loaded: its `version` is the optimistic lock.
 */
export type StagedChange =
  | { op: 'update'; key: string; rule: RbacRule; cells: Partial<RbacCells> }
  | { op: 'delete'; key: string; rule: RbacRule }
  | { op: 'create'; key: string; sheet: string; cells: Partial<RbacCells> };

export type RowState = 'clean' | 'modified' | 'new' | 'deleted';

export interface GridRow extends CellSource {
  key: string;
  /** The rule the staged change was made against (else the loaded rule); null for a staged new row. */
  rule: RbacRule | null;
  /** Someone saved the row after this change was staged; committing will report the conflict. */
  stale: boolean;
  /** Values before staged edits, for "was" hints. */
  original: CellSource | null;
  state: RowState;
  changed: ReadonlySet<string>;
  pendingRequestIds: string[];
  /** 1-based position in the sheet; new rows continue after the last loaded row. */
  number: number;
}

const NONE: ReadonlySet<string> = new Set();

export function buildRows(rules: RbacRule[], staged: StagedChange[]): GridRow[] {
  const byKey = new Map(staged.map((s) => [s.key, s]));
  const rows: GridRow[] = rules.map((loaded, i) => {
    const s = byKey.get(loaded.id);
    // Keep the staged version even when a reload brings a newer one, so the version check still sees the conflict.
    const rule = s && s.op !== 'create' ? s.rule : loaded;
    const original = ruleCells(rule);
    const base: GridRow = {
      key: loaded.id,
      rule,
      stale: loaded.version > rule.version,
      original,
      cells: original.cells,
      extra: original.extra,
      state: 'clean',
      changed: NONE,
      pendingRequestIds: loaded.pending_request_ids ?? [],
      number: i + 1,
    };
    if (s?.op === 'delete') return { ...base, state: 'deleted' };
    if (s?.op === 'update') {
      return { ...base, cells: { ...original.cells, ...s.cells }, state: 'modified', changed: new Set(Object.keys(s.cells)) };
    }
    return base;
  });
  let added = 0;
  for (const s of staged) {
    if (s.op !== 'create') continue;
    added += 1;
    rows.push({
      key: s.key,
      rule: null,
      stale: false,
      original: null,
      cells: s.cells,
      extra: {},
      state: 'new',
      changed: NONE,
      pendingRequestIds: [],
      number: rules.length + added,
    });
  }
  return rows;
}

function replaceAt(staged: StagedChange[], index: number, next: StagedChange | null): StagedChange[] {
  if (index < 0) return next ? [...staged, next] : staged;
  if (!next) return staged.filter((_, i) => i !== index);
  return staged.map((s, i) => (i === index ? next : s));
}

function withoutUnchanged(cells: Partial<RbacCells>, base: Partial<RbacCells>): Partial<RbacCells> {
  return Object.fromEntries(
    Object.entries(cells).filter(([field, value]) => value !== (base[field as RbacField] ?? '')),
  ) as Partial<RbacCells>;
}

export function stageEdit(staged: StagedChange[], row: GridRow, field: RbacField, value: string): StagedChange[] {
  const index = staged.findIndex((s) => s.key === row.key);
  const prev = index >= 0 ? staged[index] : null;
  if (prev?.op === 'create') {
    return replaceAt(staged, index, { ...prev, cells: { ...prev.cells, [field]: value } });
  }
  if (prev?.op === 'delete' || !row.rule || !row.original) return staged;
  const cells = withoutUnchanged(
    { ...(prev?.op === 'update' ? prev.cells : {}), [field]: value },
    row.original.cells,
  );
  return replaceAt(
    staged,
    index,
    Object.keys(cells).length ? { op: 'update', key: row.key, rule: row.rule, cells } : null,
  );
}

export function stageDelete(staged: StagedChange[], row: GridRow): StagedChange[] {
  const index = staged.findIndex((s) => s.key === row.key);
  if (row.state === 'new') return replaceAt(staged, index, null);
  if (!row.rule) return staged;
  return replaceAt(staged, index, { op: 'delete', key: row.key, rule: row.rule });
}

export function stageUndo(staged: StagedChange[], key: string): StagedChange[] {
  return staged.filter((s) => s.key !== key);
}

let tempCounter = 0;

/** A temporary id for a staged new row (the backend assigns the real one on commit). */
export function newRowKey(): string {
  tempCounter += 1;
  return `new-${Date.now().toString(36)}-${tempCounter}`;
}

export function stageCreate(staged: StagedChange[], key: string, sheet: string, cells: Partial<RbacCells>): StagedChange[] {
  return [...staged, { op: 'create', key, sheet, cells }];
}

/**
 * Cells for an added row: every column the sheet models, with environment and owner copied
 * from its first row; an empty environment sheet starts with its own environment.
 */
export function newRowCells(sheet: RbacSheetInfo, rules: RbacRule[]): Partial<RbacCells> {
  const first = rules[0];
  const cells: Partial<RbacCells> = {};
  for (const col of sheet.columns) {
    if (!col.field) continue;
    cells[col.field] = first && (col.field === 'environment_value' || col.field === 'owner') ? first[col.field] ?? '' : '';
  }
  if (!first && sheet.environment && 'environment_value' in cells) cells.environment_value = sheet.environment;
  return cells;
}

/** Copy a row as a new staged row (all modelled cells). */
export function duplicateCells(sheet: RbacSheetInfo, row: GridRow): Partial<RbacCells> {
  const cells: Partial<RbacCells> = {};
  for (const col of sheet.columns) if (col.field) cells[col.field] = row.cells[col.field] ?? '';
  return cells;
}

/**
 * After a 409, carry the user's change onto the row as it is now: edits that the
 * other person already made drop out; nothing left means there's nothing to save.
 */
export function rebaseChange(staged: StagedChange[], current: RbacRule): StagedChange[] {
  const index = staged.findIndex((s) => s.key === current.id);
  if (index < 0) return staged;
  const s = staged[index];
  if (s.op === 'delete') return replaceAt(staged, index, { ...s, rule: current });
  if (s.op !== 'update') return staged;
  const cells = withoutUnchanged(s.cells, ruleCells(current).cells);
  return replaceAt(staged, index, Object.keys(cells).length ? { ...s, rule: current, cells } : null);
}

export function toRowChange(s: StagedChange): RbacRowChange {
  if (s.op === 'create') return { op: 'create', sheet: s.sheet, cells: s.cells };
  if (s.op === 'delete') return { op: 'delete', rule_id: s.rule.id, version: s.rule.version };
  return { op: 'update', rule_id: s.rule.id, version: s.rule.version, cells: s.cells };
}

export function countByOp(staged: StagedChange[]): Record<StagedChange['op'], number> {
  const counts = { create: 0, update: 0, delete: 0 };
  for (const s of staged) counts[s.op] += 1;
  return counts;
}
