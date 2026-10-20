import type { ChangeOp, SecurableType, Tags } from '../../../types/metadata';
import { nameProblem } from './format';

/** A name with `*` or `?` is a pattern; the server expands it into one op per known object. */
export const isPattern = (name: string): boolean => /[*?]/.test(name);

const UNSAFE = /[`'";\s]/;

/** Mirrors the backend's pattern checks (expand.py); returns a message or null. */
export function patternProblem(pattern: string, column?: string | null): string | null {
  const parts = pattern.trim().split('.');
  if (!pattern.trim() || parts.length > 3 || parts.some((p) => !p.trim())) {
    return 'Use catalog, catalog.schema or catalog.schema.table, with * or ? in any part.';
  }
  if (parts.some((p) => UNSAFE.test(p.trim()))) return 'Patterns can’t contain quotes, semicolons or spaces.';
  if (column?.trim() && parts.length !== 3) return 'A column needs a catalog.schema.table pattern.';
  if (column && column.includes('`')) return 'Column names can’t contain backticks.';
  return null;
}

/**
 * `domain=sales; pii` → `{domain: 'sales', pii: null}`. Entries are separated by `;` or new lines;
 * a key without `=` is a key-only tag. A JSON object is accepted too.
 */
export function parseTagList(text: string): { tags: Tags; error: string | null } {
  const raw = text.trim();
  if (!raw) return { tags: {}, error: null };
  if (raw.startsWith('{')) {
    try {
      const data = JSON.parse(raw) as unknown;
      if (!data || typeof data !== 'object' || Array.isArray(data)) return { tags: {}, error: 'Tags must be a JSON object.' };
      const tags: Tags = {};
      for (const [k, v] of Object.entries(data as Record<string, unknown>)) {
        if (v !== null && typeof v !== 'string') return { tags: {}, error: `The value of ${k} must be text or null.` };
        tags[k] = v;
      }
      return { tags, error: null };
    } catch {
      return { tags: {}, error: 'That isn’t valid JSON.' };
    }
  }
  const tags: Tags = {};
  for (const entry of raw.split(/[;\n]/)) {
    const part = entry.trim();
    if (!part) continue;
    const eq = part.indexOf('=');
    const key = (eq < 0 ? part : part.slice(0, eq)).trim();
    if (!key) return { tags: {}, error: `“${part}” has no key.` };
    if (/\s/.test(key)) return { tags: {}, error: `Tag keys can’t contain spaces (“${key}”).` };
    tags[key] = eq < 0 ? null : part.slice(eq + 1).trim();
  }
  return { tags, error: null };
}

/** Splits CSV or tab-separated text (pasted from a spreadsheet) into rows, honouring double quotes. */
export function splitRows(text: string): string[][] {
  const firstLine = text.split(/\r?\n/, 1)[0] ?? '';
  const sep = firstLine.includes('\t') ? '\t' : ',';
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"' && cell === '') quoted = true;
    else if (ch === sep) {
      row.push(cell);
      cell = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = '';
    } else cell += ch;
  }
  if (cell !== '' || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

const NAME_HEADERS = ['name', 'object', 'table', 'full_name', 'full name', 'table name', 'table_name', 'fqn'];
const COLUMN_HEADERS = ['column', 'column_name', 'column name'];
const COMMENT_HEADERS = ['comment', 'description'];
const TAGS_HEADERS = ['tags'];
const MAX_ERRORS = 10;

export interface SheetParse {
  ops: ChangeOp[];
  /** Data rows read (blank rows skipped). */
  rows: number;
  /** Distinct objects or patterns the ops touch. */
  targets: number;
  patterns: number;
  /** Headers read as tag keys, in sheet order. */
  tagKeys: string[];
  errors: string[];
}

/**
 * Reads a pasted sheet with a header row. One column names the object (`name`, `table`…), and
 * `column`, `comment`/`description` and `tags` (`k=v; k2`) are optional. Every other header is a
 * tag key, and a non-empty cell sets that tag. Names with `*` or `?` are patterns.
 */
export function parseSheet(text: string): SheetParse | null {
  if (!text.trim()) return null;
  const [header, ...body] = splitRows(text.replace(/^\uFEFF/, '')).filter((r) => r.some((c) => c.trim()));
  const result: SheetParse = { ops: [], rows: 0, targets: 0, patterns: 0, tagKeys: [], errors: [] };
  if (!header) return null;

  const heads = header.map((h) => h.trim());
  const find = (names: string[]) => heads.findIndex((h) => names.includes(h.toLowerCase()));
  const nameAt = find(NAME_HEADERS);
  if (nameAt < 0) {
    result.errors.push('The first row must be a header with a name column (or table), e.g. name, data_owner, domain.');
    return result;
  }
  const columnAt = find(COLUMN_HEADERS);
  const commentAt = find(COMMENT_HEADERS);
  const tagsAt = find(TAGS_HEADERS);
  const tagCols = heads
    .map((h, i) => ({ key: h, i }))
    .filter(({ key, i }) => key && ![nameAt, columnAt, commentAt, tagsAt].includes(i));
  const badKey = tagCols.find(({ key }) => /\s/.test(key));
  if (badKey) {
    result.errors.push(`Header “${badKey.key}” has a space. Tag keys can’t contain spaces.`);
    return result;
  }
  result.tagKeys = tagCols.map((c) => c.key);

  const tagOps = new Map<string, ChangeOp>();
  const commentOps = new Map<string, ChangeOp>();
  const targetKey = (name: string, column: string | null) => `${name.toLowerCase()}\u0000${column ?? ''}`;
  const error = (msg: string) => {
    if (result.errors.length < MAX_ERRORS) result.errors.push(msg);
  };

  body.forEach((cells, n) => {
    const line = `Row ${n + 2}`;
    const cell = (i: number) => (i >= 0 ? (cells[i] ?? '').trim() : '');
    result.rows++;
    let name = cell(nameAt);
    let column: string | null = cell(columnAt) || null;
    if (!name) return error(`${line}: the name is empty.`);
    const parts = name.split('.');
    if (parts.length === 4 && columnAt < 0) {
      column = parts.pop() ?? null;
      name = parts.join('.');
    }
    const pattern = isPattern(name);
    const problem = pattern ? patternProblem(name, column) : nameProblem(name, column);
    if (problem) return error(`${line} (${name}): ${problem}`);

    const tags: Tags = {};
    if (tagsAt >= 0 && cell(tagsAt)) {
      const parsed = parseTagList(cell(tagsAt));
      if (parsed.error) return error(`${line}: ${parsed.error}`);
      Object.assign(tags, parsed.tags);
    }
    for (const { key, i } of tagCols) {
      const value = cell(i);
      if (value) tags[key] = value;
    }
    const comment = cell(commentAt);
    if (!Object.keys(tags).length && !comment) return;

    const id = targetKey(name, column);
    const target = pattern ? { name: '', match: name } : { name };
    if (Object.keys(tags).length) {
      const existing = tagOps.get(id);
      if (existing) existing.tags = { ...existing.tags, ...tags };
      else tagOps.set(id, { op: 'set_tags', ...target, column, tags });
    }
    if (comment) commentOps.set(id, { op: 'set_comment', ...target, column, comment });
  });

  result.ops = [...tagOps.values(), ...commentOps.values()];
  const ids = new Set([...tagOps.keys(), ...commentOps.keys()]);
  result.targets = ids.size;
  result.patterns = new Set(result.ops.filter((o) => o.match).map((o) => targetKey(o.match ?? '', o.column ?? null))).size;
  return result;
}

/** A single pattern op for the pattern form. */
export function patternOp(args: {
  pattern: string;
  column: string;
  types: SecurableType[];
  action: 'set_tags' | 'unset_tags' | 'set_comment';
  tags: Tags;
  comment: string;
}): ChangeOp {
  const base: ChangeOp = {
    op: args.action,
    name: '',
    match: args.pattern.trim(),
    column: args.column.trim() || null,
    match_types: args.column.trim() ? [] : args.types,
  };
  if (args.action === 'set_tags') return { ...base, tags: args.tags };
  if (args.action === 'unset_tags') return { ...base, keys: Object.keys(args.tags) };
  return { ...base, comment: args.comment };
}
