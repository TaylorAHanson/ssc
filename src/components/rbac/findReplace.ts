import type { RbacField, RbacSheetInfo } from '../../types/rbac';
import type { GridRow } from './staging';
import { PACKED_FIELDS, cellValue } from './utils';

export interface FindOptions {
  find: string;
  replace: string;
  matchCase: boolean;
  /** Match the whole cell, or in a packed cell one whole `;`-separated value, not text inside it. */
  wholeValue: boolean;
}

/** An editable cell holding the text, and what it becomes; `next` equals `value` when the replacement changes nothing. */
export interface FindMatch {
  row: GridRow;
  col: number;
  field: RbacField;
  value: string;
  next: string;
}

const escapeRegExp = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Builds the per-cell replacer once per search; it returns null when the cell doesn't match. */
export function cellReplacer(o: FindOptions): ((value: string, packed: boolean) => string | null) | null {
  if (!o.find) return null;
  const key = (s: string) => (o.matchCase ? s.trim() : s.trim().toLowerCase());
  if (o.wholeValue) {
    const target = key(o.find);
    if (!target) return null;
    return (value, packed) => {
      if (!packed) return key(value) === target ? o.replace : null;
      const parts = value.split(';');
      if (!parts.some((p) => key(p) === target)) return null;
      // Unmatched values keep their exact spelling and spacing; a blank replacement drops the value,
      // and one already in the cell isn't added twice.
      const replacement = o.replace.trim();
      const others = new Set(parts.filter((p) => key(p) !== target).map(key).filter(Boolean));
      let placed = false;
      const out: string[] = [];
      for (const p of parts) {
        if (key(p) !== target) out.push(p);
        else if (replacement && !placed && !others.has(key(replacement))) {
          out.push(p.replace(p.trim(), () => replacement));
          placed = true;
        }
      }
      return out.join(';');
    };
  }
  const pattern = new RegExp(escapeRegExp(o.find), o.matchCase ? 'g' : 'gi');
  return (value) => {
    pattern.lastIndex = 0;
    if (!pattern.test(value)) return null;
    // A function, so `$` in the replacement is taken literally.
    return value.replace(pattern, () => o.replace);
  };
}

/** Columns find and replace can change: modelled ones; imported-only columns are read-only. */
export function replaceableColumns(sheet: RbacSheetInfo): { col: number; field: RbacField; header: string }[] {
  return sheet.columns.flatMap((c, col) => (c.field ? [{ col, field: c.field, header: c.header }] : []));
}

/**
 * Every matching cell in the rows shown, in table order (row, then column). `scope` is a column
 * index, or null for every replaceable column. Deleted rows are skipped.
 */
export function findMatches(sheet: RbacSheetInfo, rows: GridRow[], scope: number | null, o: FindOptions): FindMatch[] {
  const replacer = cellReplacer(o);
  if (!replacer) return [];
  const columns = replaceableColumns(sheet).filter((c) => scope === null || c.col === scope);
  const matches: FindMatch[] = [];
  for (const row of rows) {
    if (row.state === 'deleted') continue;
    for (const { col, field } of columns) {
      const value = cellValue(row, sheet.columns[col], col);
      const next = replacer(value, PACKED_FIELDS.has(field));
      if (next !== null) matches.push({ row, col, field, value, next });
    }
  }
  return matches;
}
