import { format, formatDistanceToNow, parseISO } from 'date-fns';
import { RbacApiError, api } from '../../services/api';
import type {
  RbacChangeSetPreview,
  RbacField,
  RbacPlane,
  RbacRule,
  RbacRowSnapshot,
  RbacSheetColumn,
  RbacSheetInfo,
} from '../../types/rbac';

export const SETTINGS_PATH = '/admin/settings?group=Access%20Rules';

export const PLANE_ORDER: RbacPlane[] = ['uc', 'workspace', 'uc_metastore'];

export const PLANE_GROUP_LABELS: Record<RbacPlane, string> = {
  uc: 'UC securables',
  workspace: 'Workspace securables',
  uc_metastore: 'UC metastore',
};

// Generic names for when a sheet's own header text isn't at hand (e.g. History spans sheets).
export const FIELD_LABELS: Record<RbacField, string> = {
  environment_value: 'Environment',
  securable_type: 'Securable type',
  catalog: 'Catalog',
  schema_name: 'Schema',
  object_name: 'Object',
  workspace_name: 'Workspace',
  owner: 'Owner',
  principal_type: 'Principal type',
  principal_name: 'Principal',
  privilege: 'Privilege',
};

// Cells that hold several values separated by ";" (principals, privilege sets).
export const PACKED_FIELDS: ReadonlySet<string> = new Set<RbacField>(['principal_name', 'privilege']);

export const splitPacked = (value: string | null | undefined): string[] =>
  (value ?? '').split(';').map((s) => s.trim()).filter(Boolean);

export interface CellSource {
  cells: Partial<Record<RbacField, string>>;
  extra: Record<string, string>;
}

/** A cell's text: a modelled field, or the raw imported value of an unmodelled column. */
export function cellValue(row: CellSource, column: RbacSheetColumn, index: number): string {
  return column.field ? row.cells[column.field] ?? '' : row.extra[String(index)] ?? '';
}

export function ruleCells(rule: RbacRule): CellSource {
  const cells: Partial<Record<RbacField, string>> = {};
  for (const field of Object.keys(FIELD_LABELS) as RbacField[]) cells[field] = rule[field] ?? '';
  return { cells, extra: rule.extra ?? {} };
}

/** Label for a diff/finding field: the sheet's own header when known, else a generic name. */
export function fieldLabel(field: string, sheet?: RbacSheetInfo | null): string {
  if (sheet) {
    const index = sheet.columns.findIndex((c) => c.field === field);
    if (index >= 0) return sheet.columns[index].header;
    if (/^\d+$/.test(field)) {
      const col = sheet.columns[Number(field)];
      if (col) return col.header;
    }
  }
  if (field in FIELD_LABELS) return FIELD_LABELS[field as RbacField];
  return /^\d+$/.test(field) ? `Column ${Number(field) + 1}` : field;
}

/** A short "what" line for a row snapshot, e.g. `schema · main.sales` or `workspace · ws-dev`. */
export function securableSummary(snap: RbacRowSnapshot | null | undefined): string {
  if (!snap) return '';
  const path = [snap.catalog, snap.schema_name, snap.object_name].filter(Boolean).join('.');
  const where = [snap.workspace_name, path].filter(Boolean).join(' · ');
  return [snap.securable_type, where].filter(Boolean).join(' · ');
}

// The backend serializes naive UTC datetimes (no timezone suffix).
export const parseUtc = (value: string): Date =>
  parseISO(/Z|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`);

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

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;

export function errorMessage(e: unknown, fallback = 'Something went wrong'): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

export interface RbacConflictDetail {
  message: string;
  rule_id: string;
  current: RbacRule | null;
}

/** The 409 body when a row changed since it was loaded. */
export function conflictOf(e: unknown): RbacConflictDetail | null {
  if (!(e instanceof RbacApiError) || e.status !== 409) return null;
  const d = e.detail;
  if (d && typeof d === 'object' && 'rule_id' in d) {
    const detail = d as Partial<RbacConflictDetail>;
    return { message: detail.message || e.message, rule_id: String(detail.rule_id), current: detail.current ?? null };
  }
  return { message: e.message, rule_id: '', current: null };
}

/** The 422 body when policy blocked a commit. */
export function blockedPreviewOf(e: unknown): RbacChangeSetPreview | null {
  if (!(e instanceof RbacApiError) || e.status !== 422) return null;
  const d = e.detail;
  return d && typeof d === 'object' && 'preview' in d ? (d as { preview: RbacChangeSetPreview }).preview : null;
}

const SOURCE_LABELS: Record<string, string> = {
  import: 'Import',
  request: 'Request',
  admin_edit: 'Direct edit',
  revert: 'Revert',
};

export const sourceLabel = (source: string | null | undefined): string =>
  source ? SOURCE_LABELS[source] ?? source.replace(/_/g, ' ') : '—';

/** Grouped by type; within a type the backend's order (environment order) is kept. */
export function sortSheets(sheets: RbacSheetInfo[]): RbacSheetInfo[] {
  return [...sheets].sort((a, b) => PLANE_ORDER.indexOf(a.plane) - PLANE_ORDER.indexOf(b.plane));
}

/** Stable loader for `useLoad`. */
export const loadSheets = (): Promise<RbacSheetInfo[]> => api.getRbacSheets().then(sortSheets);
