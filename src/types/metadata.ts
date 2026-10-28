// Metadata Manager contract (ADR-0006). Mirrors backend/app/services/metadata/types.py
// field for field; timestamps arrive as ISO strings. Add fields as optional; don't
// rename or remove them.

export type SecurableType =
  | 'catalog'
  | 'schema'
  | 'table'
  | 'view'
  | 'materialized_view'
  | 'streaming_table'
  | 'metric_view'
  | 'column';
export const RELATION_TYPES: SecurableType[] = ['table', 'view', 'materialized_view', 'streaming_table', 'metric_view'];
export type Level = 'catalog' | 'schema' | 'relation' | 'column';
export type ActorType = 'user' | 'service_principal' | 'system';
export type ChangeSource = 'ui' | 'api' | 'mcp' | 'agent' | 'adopt' | 'import' | 'system' | 'revert';
export type ObjectStatus = 'active' | 'deleted';
export type ObjectState = 'exists' | 'pending' | 'orphaned';
export type ApplyStatus = 'pending' | 'applied' | 'failed' | 'skipped';
export type ReconcileMode = 'off' | 'plan' | 'enforce';
export type Severity = 'deny' | 'warn' | 'info';

export const COLUMN_SEPARATOR = '::';

/** A tag map; `null` is a key-only tag. */
export type Tags = Record<string, string | null>;

// --- Identity ---------------------------------------------------------------------

export interface ObjectKey {
  catalog: string;
  schema_name?: string | null;
  object_name?: string | null;
  column_name?: string | null;
}

export interface Authorship {
  set_by?: string | null;
  set_by_type?: ActorType | null;
  source?: ChangeSource | null;
  set_at?: string | null;
  change_set_id?: string | null;
}

export interface TagAssignment extends Authorship {
  key: string;
  value: string | null;
  version: number;
}

export interface CommentState extends Authorship {
  value: string | null;
  /** 0 = never set. */
  version: number;
}

export interface DesiredState {
  id: string;
  key: ObjectKey;
  fqn_key: string;
  display_name: string;
  securable_type: SecurableType;
  environment?: string | null;
  tags: Tags;
  tag_details: TagAssignment[];
  comment: CommentState;
  status: ObjectStatus;
  object_state: ObjectState;
  orphaned_at?: string | null;
  created_at?: string | null;
  created_by?: string | null;
  created_by_type?: ActorType | null;
  updated_at?: string | null;
  last_applied_at?: string | null;
  last_apply_status?: ApplyStatus | null;
  last_apply_error?: string | null;
  last_observed_at?: string | null;
  drift_detected_at?: string | null;
  desired_hash?: string | null;
}

export interface ObservedState {
  fqn_key: string;
  key: ObjectKey;
  securable_type: SecurableType;
  table_type?: string | null;
  data_type?: string | null;
  ordinal_position?: number | null;
  owner?: string | null;
  comment?: string | null;
  tags: Tags;
  present: boolean;
  created?: string | null;
  last_altered?: string | null;
  observed_at?: string | null;
  missing_since?: string | null;
}

// --- Actors and principals ----------------------------------------------------------

export type ChangeOpType = 'set_tags' | 'unset_tags' | 'replace_tags' | 'set_comment' | 'clear_comment';

export interface PrincipalScope {
  application_id: string;
  display_name: string;
  description?: string | null;
  enabled: boolean;
  /** Name globs: "sales_*", "sales_dev.bronze", "*.bronze.*". Empty = nothing. */
  scopes: string[];
  /** Tag-key globs. Empty = any key the key policies allow. */
  allowed_keys: string[];
  /** Empty = all operations. */
  allowed_operations: ChangeOpType[];
  /** Empty = all environments. */
  environments: string[];
  max_objects_per_change?: number | null;
  can_skip_approval: boolean;
}

export type PrincipalIn = PrincipalScope;

export interface PrincipalOut extends PrincipalScope {
  last_seen_at?: string | null;
  last_seen_headers?: Record<string, unknown> | null;
  created_at?: string | null;
  created_by?: string | null;
  updated_at?: string | null;
  updated_by?: string | null;
}

export interface Actor {
  id: string;
  actor_type: ActorType;
  display_name?: string | null;
  email?: string | null;
  roles: string[];
  groups: string[];
  is_admin: boolean;
  principal?: PrincipalScope | null;
}

export interface WhoAmI {
  actor: Actor;
  recognized_as: 'user' | 'service_principal' | 'unregistered_service_principal';
  headers_seen: string[];
  identifiers: Record<string, string | null>;
  can_browse: boolean;
  /** full = every object; owned = only objects the caller owns; none = no access. */
  browse_scope?: 'full' | 'owned' | 'none';
  message?: string | null;
}

// --- Changes -------------------------------------------------------------------------

export interface ChangeOp {
  op: ChangeOpType;
  name: string;
  column?: string | null;
  tags?: Tags;
  keys?: string[];
  comment?: string | null;
  /** Tag key -> the version last seen (0 = must not exist yet). A mismatch is a 409. */
  expected_versions?: Record<string, number>;
  expected_comment_version?: number | null;
  /** Type hint for an object that doesn't exist yet. */
  securable_type?: SecurableType | null;
  /**
   * A name glob instead of `name` (send `name: ''`): `sales_dev.*.*`, `*` and `?` within one part.
   * The server expands it into one op per known object at that level; `column` is then a glob too.
   */
  match?: string | null;
  /** With `match`: only these types (empty = every type at the glob's level). */
  match_types?: SecurableType[];
}

export type ApprovalRouting = 'request' | 'split' | 'reject';

export interface ChangeRequest {
  ops: ChangeOp[];
  reason?: string;
  environments?: string[];
  on_approval_needed?: ApprovalRouting;
  idempotency_key?: string | null;
}

export interface PromoteRequest {
  name: string;
  column?: string | null;
  include_columns?: boolean;
  to_environments: string[];
  include_tags?: boolean;
  include_comment?: boolean;
  reason?: string;
  on_approval_needed?: ApprovalRouting;
  idempotency_key?: string | null;
}

export interface RevertRequest {
  reason?: string;
  on_approval_needed?: ApprovalRouting;
}

export type ItemAction = 'set' | 'unset' | 'comment_set' | 'comment_clear';

export interface ChangeItem {
  fqn_key: string;
  name: string;
  column?: string | null;
  securable_type: SecurableType;
  environment?: string | null;
  field: 'tag' | 'comment';
  key?: string | null;
  action: ItemAction;
  before?: string | null;
  before_exists: boolean;
  before_version: number;
  after?: string | null;
  object_exists: boolean;
  object_state: ObjectState;
  /** The change starts managing an existing object, and Unity Catalog already has this value. */
  unchanged_in_uc?: boolean;
  /** The caller can't browse this object, so `before` is withheld. */
  before_hidden?: boolean;
  copied_from?: string | null;
}

export interface Finding {
  code: string;
  severity: Severity;
  message: string;
  fqn_key?: string | null;
  key?: string | null;
}

export interface AuthzDecision {
  allowed: boolean;
  needs_approval: boolean;
  can_request: boolean;
  via: 'admin' | 'owner' | 'uc_owner' | 'principal' | 'none';
  reasons: string[];
  sensitive_reasons: string[];
  approver_groups: string[];
  owner_source?: string | null;
}

export interface ItemDecision {
  item: ChangeItem;
  decision: AuthzDecision;
  findings: Finding[];
}

export interface ChangePreview {
  items: ItemDecision[];
  findings: Finding[];
  blocked: boolean;
  direct_count: number;
  approval_count: number;
  denied_count: number;
  noop_count: number;
  /** Of direct_count: items that are `unchanged_in_uc`. */
  kept_count?: number;
  needs_approval: boolean;
  approver_groups: string[];
  environments: string[];
  object_count: number;
  digest: string;
  report_markdown: string;
  risk?: Record<string, unknown> | null;
}

export interface ChangeEvent {
  id: number;
  change_set_id: string;
  object_id: string;
  fqn_key: string;
  name?: string | null;
  action: string;
  key?: string | null;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  actor?: string | null;
  actor_type?: ActorType | null;
  source: string;
  created_at?: string | null;
}

export interface ChangeSet {
  id: string;
  actor: string;
  actor_type: ActorType;
  source: ChangeSource;
  reason?: string | null;
  idempotency_key?: string | null;
  request_id?: string | null;
  reverts_change_set_id?: string | null;
  reverted_by?: string | null;
  summary: Record<string, unknown>;
  created_at?: string | null;
  events: ChangeEvent[];
}

export interface ChangeSetPage {
  items: ChangeSet[];
  total: number;
  page: number;
  page_size: number;
}

export type ChangeStatus = 'committed' | 'pending_approval' | 'partial' | 'noop' | 'rejected';

export interface ChangeResult {
  status: ChangeStatus;
  change_set?: ChangeSet | null;
  request_id?: string | null;
  preview: ChangePreview;
  message: string;
  replayed: boolean;
}

export interface VersionConflictItem {
  fqn_key: string;
  name: string;
  key?: string | null;
  expected_version: number;
  current_version: number;
  current_value?: string | null;
}

// --- Reconciler ----------------------------------------------------------------------

export type PlanOperation = 'set' | 'unset' | 'comment';
export type PlanStatus = 'planned' | 'applied' | 'noop' | 'failed' | 'skipped';
export type RunTrigger = 'queue' | 'sweep' | 'manual' | 'new_object' | 'notify';
export type RunStatus = 'running' | 'completed' | 'partial' | 'failed' | 'paused' | 'skipped';

export interface PlanItem {
  fqn_key: string;
  object_id?: string | null;
  name: string;
  column?: string | null;
  securable_type: SecurableType;
  operation: PlanOperation;
  tags: Tags;
  keys: string[];
  comment?: string | null;
  sql: string;
  removals: number;
}

export interface PlanOutcome extends PlanItem {
  status: PlanStatus;
  detail?: string | null;
}

export interface ReconcileResult {
  run_id: string;
  trigger: RunTrigger;
  mode: ReconcileMode;
  status: RunStatus;
  actor?: string | null;
  objects_checked: number;
  objects_drifted: number;
  statements_planned: number;
  statements_applied: number;
  statements_failed: number;
  removals_planned: number;
  skipped_catalogs: string[];
  skipped_reasons?: Record<string, string>;
  /** Catalogs this run couldn't read from Unity Catalog, so none of their objects were checked. */
  unread_catalogs?: string[];
  /** Whole catalogs; otherwise only named objects, so its counts say nothing about the rest. */
  full_check?: boolean;
  paused_reason?: string | null;
  error?: string | null;
  items: PlanOutcome[];
  started_at?: string | null;
  finished_at?: string | null;
}

export interface ReconcileRunPage {
  items: ReconcileResult[];
  total: number;
  page: number;
  page_size: number;
}

export interface ReconcileNotify {
  names: string[];
}

export interface ReconcileAccepted {
  enqueued: string[];
  rejected: Record<string, string>[];
}

export interface RunRequest {
  kind?: 'sweep' | 'plan' | 'queue';
  catalogs?: string[];
  override_circuit_breaker?: boolean;
}

// --- Search and detail ---------------------------------------------------------------

export type SortField = 'name' | 'updated_at' | 'last_applied_at' | 'drift_detected_at' | 'catalog' | 'securable_type';

/**
 * GET /objects query. Tag filters travel as repeated `tag` params:
 * `key=value`, `key` (has the key) or `!key` (lacks it).
 */
export interface SearchQuery {
  q?: string;
  catalogs?: string[];
  schemas?: string[];
  securable_types?: SecurableType[];
  environments?: string[];
  object_states?: ObjectState[];
  tags?: string[];
  /** Only objects tagged `dataset` or `data_set` with this value. */
  dataset?: string;
  drift?: boolean | null;
  include_columns?: boolean;
  editable_only?: boolean;
  include_unmanaged?: boolean;
  sort?: SortField;
  order?: 'asc' | 'desc';
  page?: number;
  page_size?: number;
}

export interface ObjectSummary {
  /** null for an observed object with no desired row. */
  id?: string | null;
  fqn_key: string;
  name: string;
  column?: string | null;
  securable_type: SecurableType;
  catalog: string;
  schema_name?: string | null;
  object_name?: string | null;
  environment?: string | null;
  managed: boolean;
  tags: Tags;
  comment?: string | null;
  observed_tags?: Tags | null;
  observed_comment?: string | null;
  object_state?: ObjectState | null;
  drift: boolean;
  last_apply_status?: ApplyStatus | null;
  last_applied_at?: string | null;
  updated_at?: string | null;
  data_type?: string | null;
  editable?: boolean | null;
  /** Tag key -> version, so edits staged from search results can carry expected_versions. */
  tag_versions?: Record<string, number> | null;
  /** The description's version (0 = never set). */
  comment_version?: number | null;
  drift_detected_at?: string | null;
}

export interface Facet {
  value: string;
  count: number;
}

/** The tables and views tagged `dataset` (or `data_set`) with one value. */
export interface DatasetSummary {
  name: string;
  table_count: number;
  catalogs: string[];
  out_of_sync_count: number;
}

export interface SearchResult {
  items: ObjectSummary[];
  total: number;
  page: number;
  page_size: number;
  /** catalog, securable_type, environment, object_state, tag_key */
  facets: Record<string, Facet[]>;
  took_ms?: number | null;
}

export interface DriftEntry {
  field: 'tag' | 'comment';
  key?: string | null;
  desired?: string | null;
  observed?: string | null;
  kind: 'missing' | 'extra' | 'changed';
  exempt: boolean;
}

export interface ObjectDetail {
  key: ObjectKey;
  fqn_key: string;
  desired?: DesiredState | null;
  observed?: ObservedState | null;
  drift: DriftEntry[];
  columns: ObjectSummary[];
  owner_groups: string[];
  owner_source?: string | null;
  editable: boolean;
  edit_reasons: string[];
  /** The same object in other environments: {"prd": "sales_prd.bronze.orders"}. */
  counterparts: Record<string, string>;
  history: ChangeEvent[];
  recent_plan: PlanOutcome[];
}

export interface KeyPolicy {
  key: string;
  applies_to: string[];
  allowed_values: string[];
  pattern?: string | null;
  editor_groups: string[];
  sensitive: boolean;
  required: boolean;
  description?: string | null;
}

export interface KeyInfo {
  key: string;
  count: number;
  values: Facet[];
  policy?: KeyPolicy | null;
  exempt: boolean;
  governed: boolean;
}

export interface EnvironmentInfo {
  key: string;
  catalog_pattern?: string | null;
  catalogs: string[];
  approval: 'sensitive' | 'all';
}

export interface MetadataConfig {
  reconcile_mode: ReconcileMode;
  owner_tag_key: string;
  exempt_keys: string[];
  key_mode: 'open' | 'known_only';
  key_policies: KeyPolicy[];
  environments: EnvironmentInfo[];
  max_objects_per_change: number;
  min_reason_length: number;
  min_justification_length: number;
  new_object_policy: 'adopt' | 'ignore';
  last_sweep_at?: string | null;
  /** Catalogs the latest full check couldn't read; their lists may be incomplete. */
  unread_catalogs?: string[];
  /** Same catalogs with a short reason the browse page can show. */
  unread_reasons?: Record<string, string>;
  circuit_breaker_tripped: boolean;
  /** Catalog names or patterns this deployment manages; empty = every catalog, but plan only. */
  managed_catalogs: string[];
  /** Set when enforce is configured but can't run because no catalogs are listed. */
  enforce_blocked_reason?: string | null;
}

// --- Adopt and import ----------------------------------------------------------------

export interface AdoptRequest {
  catalogs?: string[];
  names?: string[];
  overwrite?: boolean;
  include_columns?: boolean;
  reason?: string;
}

export interface ImportRow {
  name: string;
  column?: string | null;
  tags?: Tags | null;
  /** null = leave the description as it is; "" = clear it. */
  comment?: string | null;
}

export interface ImportRequest {
  rows?: ImportRow[];
  /** CSV with a header row: name,column,comment,tags (tags is a JSON object). */
  csv?: string | null;
  mode?: 'merge' | 'replace';
  reason?: string;
}

/** The 409 `detail`: `{detail: {message, conflicts}}`. */
export interface VersionConflictBody {
  message: string;
  conflicts: VersionConflictItem[];
}

/** The 422 `detail`: `{detail: {message, preview}}`. */
export interface ChangeBlockedBody {
  message: string;
  preview: ChangePreview;
}
