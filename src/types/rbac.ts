// Access Rules types. Mirrors backend/app/models/rbac.py.

export type RbacPlane = 'uc' | 'uc_metastore' | 'workspace';
export type RbacSeverity = 'deny' | 'warn';

export const RBAC_CANONICAL_FIELDS = [
  'environment_value',
  'securable_type',
  'catalog',
  'schema_name',
  'object_name',
  'workspace_name',
  'owner',
  'principal_type',
  'principal_name',
  'privilege',
] as const;

export type RbacField = (typeof RBAC_CANONICAL_FIELDS)[number];

export type RbacCells = Record<RbacField, string>;

export interface RbacRule extends RbacCells {
  id: string;
  sheet: string;
  plane: RbacPlane;
  environment: string | null;
  position: number;
  extra: Record<string, string>;
  status: 'active' | 'deleted';
  version: number;
  source: string | null;
  source_ref: string | null;
  created_at: string | null;
  created_by: string | null;
  updated_at: string | null;
  updated_by: string | null;
  pending_request_ids: string[];
}

export interface RbacRulesPage {
  rules: RbacRule[];
  total: number;
}

export interface RbacFinding {
  severity: RbacSeverity;
  code: string;
  message: string;
  sheet?: string | null;
  rule_id?: string | null;
  change_index?: number | null;
  row_number?: number | null;
  field?: string | null;
  principal?: string | null;
  privilege?: string | null;
}

export interface RbacRowChange {
  op: 'create' | 'update' | 'delete';
  rule_id?: string;
  version?: number;
  sheet?: string;
  cells?: Partial<RbacCells>;
  extra?: Record<string, string>;
}

export interface RbacChangeSetIn {
  changes: RbacRowChange[];
  reason: string;
}

export interface RbacRowSnapshot extends Partial<RbacCells> {
  sheet?: string;
  plane?: RbacPlane;
  environment?: string | null;
  position?: number;
  extra?: Record<string, string>;
  status?: string;
  version?: number;
}

export interface RbacRowDiff {
  op: 'create' | 'update' | 'delete' | 'restore';
  rule_id: string | null;
  sheet: string;
  plane: RbacPlane;
  environment: string | null;
  before: RbacRowSnapshot | null;
  after: RbacRowSnapshot | null;
}

export interface RbacChangeSetPreview {
  diffs: RbacRowDiff[];
  findings: RbacFinding[];
  blocked: boolean;
  two_person_required: boolean;
}

export interface RbacChangeSetResult {
  change_set_id: string | null;
  request_id: string | null;
  diffs: RbacRowDiff[];
  findings: RbacFinding[];
}

export interface RbacChangeSetSummary {
  id: string;
  source: string;
  source_ref: string | null;
  actor: string | null;
  reason: string | null;
  summary: {
    created?: number;
    updated?: number;
    deleted?: number;
    sheets?: string[];
    planes?: RbacPlane[];
    [key: string]: unknown;
  };
  reverted_by: string | null;
  created_at: string | null;
}

export interface RbacChangeSetDetail extends RbacChangeSetSummary {
  diffs: RbacRowDiff[];
}

export interface RbacHistoryPage {
  change_sets: RbacChangeSetSummary[];
  total: number;
}

export interface RbacPlanePermission {
  plane: RbacPlane;
  label: string;
  approver_group: string | null;
  group_status: 'unset' | 'found' | 'not_found' | 'unknown';
  can_edit: boolean;
}

export interface RbacPermissions {
  can_view: boolean;
  can_import: boolean;
  can_reset: boolean;
  can_export: boolean;
  planes: RbacPlanePermission[];
  min_reason_length: number;
  max_changes_per_commit: number;
}

export interface RbacSheetColumn {
  header: string;
  field: RbacField | null;
}

export interface RbacSheetInfo {
  name: string;
  plane: RbacPlane;
  environment: string | null;
  columns: RbacSheetColumn[];
  row_count: number;
  export_target: string;
  export_path: string;
  editable: boolean;
}

export interface RbacImportSheetSummary {
  sheet: string;
  plane: RbacPlane;
  environment: string | null;
  rows: number;
  columns: RbacSheetColumn[];
  unknown_headers: string[];
  duplicates: number;
  warnings: number;
}

export interface RbacImportPreview {
  import_id: string;
  filename: string | null;
  file_sha256: string;
  sheets: RbacImportSheetSummary[];
  skipped_sheets: string[];
  missing_sheets: string[];
  findings: RbacFinding[];
  findings_total: number;
  existing_rules: number;
  mode: 'initial' | 'reset';
  reset_allowed: boolean;
  in_flight_requests: number;
  confirmation_phrase: string | null;
}

export interface RbacImportResult {
  import_id: string;
  change_set_id: string | null;
  created: number;
  deleted: number;
  sheets: RbacImportSheetSummary[];
}

export interface RbacImportSummary {
  id: string;
  filename: string | null;
  mode: string | null;
  status: string;
  actor: string | null;
  created_at: string | null;
  completed_at: string | null;
  change_set_id: string | null;
  error: string | null;
}

export interface RbacExportFile {
  id: string;
  sheet: string;
  path: string | null;
  sha256: string | null;
  row_count: number;
  size_bytes: number;
  status: 'written' | 'skipped' | 'failed';
  error: string | null;
  downloadable: boolean;
}

export interface RbacExportRun {
  id: string;
  trigger: 'schedule' | 'manual' | 'on_change';
  actor: string | null;
  status: 'running' | 'completed' | 'partial' | 'skipped' | 'failed';
  watermark: number;
  change_summary: Record<string, unknown>;
  warnings: string[];
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  files: RbacExportFile[];
}

export interface RbacExportTarget {
  key: string;
  label: string;
  path: string;
  sheets: string[];
  configured: boolean;
}

export interface RbacExportStatus {
  runs: RbacExportRun[];
  cron: string;
  next_run_at: string | null;
  targets: RbacExportTarget[];
  pending_changes: boolean;
}

export interface RbacPathCheckResult {
  target: string;
  path: string;
  ok: boolean;
  message: string;
}

export interface RbacCompareDifference {
  kind: string;
  detail: string;
}

export interface RbacCompareResult {
  sheet: string;
  identical: boolean;
  ours_sha256: string;
  theirs_sha256: string;
  differences: RbacCompareDifference[];
  only_in_ours: string[][];
  only_in_theirs: string[][];
}
