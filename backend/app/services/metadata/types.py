"""Metadata Manager DTOs (ADR-0006), shared by the services, the API, the tools and the UI.

This module is the frozen contract between the workstreams: ``src/types/metadata.ts``
mirrors it field for field. Add fields with defaults; don't rename or remove them.

Vocabulary:

* an *object* is one securable with desired state: a catalog, schema, table-like
  relation or column, identified by its ``fqn_key``;
* a *change op* is what a caller asks for (``set_tags`` on a name, ...);
* a *change item* is one resolved tag or description edit on one object, the unit
  authorization, the log and approvals reason about;
* a *plan item* is one statement the reconciler would run against Unity Catalog.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SecurableType = Literal[
    "catalog", "schema", "table", "view", "materialized_view", "streaming_table", "metric_view", "column",
]
RELATION_TYPES: tuple = ("table", "view", "materialized_view", "streaming_table", "metric_view")
Level = Literal["catalog", "schema", "relation", "column"]
ActorType = Literal["user", "service_principal", "system"]
ChangeSource = Literal["ui", "api", "mcp", "agent", "adopt", "import", "system", "revert"]
ObjectStatus = Literal["active", "deleted"]
ObjectState = Literal["exists", "pending", "orphaned"]
ApplyStatus = Literal["pending", "applied", "failed", "skipped"]
ReconcileMode = Literal["off", "plan", "enforce"]
Severity = Literal["deny", "warn", "info"]

COLUMN_SEPARATOR = "::"
SYSTEM_ACTOR = "system"

# Names reach generated SQL and come from callers, so they're untrusted. Name parts
# are emitted backtick-quoted, but anything that could end a statement or open a
# string is still refused; column names may contain spaces.
_UNSAFE_NAME_PART = re.compile(r"[`'\";\s\x00-\x1f\x7f]")
_UNSAFE_COLUMN_NAME = re.compile(r"[`\x00-\x1f\x7f]")


def normalize_value(value: Optional[str]) -> Optional[str]:
    """UC reports a key-only tag as ``""``; both sides store it as None."""
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def normalize_tags(tags: Optional[Dict[str, Optional[str]]]) -> Dict[str, Optional[str]]:
    return {str(k): normalize_value(v) for k, v in (tags or {}).items() if str(k).strip()}


# --- Identity -------------------------------------------------------------------------


class ObjectKey(BaseModel):
    """A securable's name, as written. ``fqn_key`` is the normalized identity."""

    model_config = ConfigDict(frozen=True)

    catalog: str
    schema_name: Optional[str] = None
    object_name: Optional[str] = None
    column_name: Optional[str] = None

    @classmethod
    def parse(cls, name: str, column: Optional[str] = None) -> "ObjectKey":
        """``catalog``, ``catalog.schema`` or ``catalog.schema.object``, plus an optional column.

        Raises ValueError for a malformed or unsafe name.
        """
        parts = [p.strip() for p in (name or "").strip().split(".")]
        if not 1 <= len(parts) <= 3 or not all(parts):
            raise ValueError(f"Invalid name '{name}'. Use catalog, catalog.schema or catalog.schema.table.")
        if any(_UNSAFE_NAME_PART.search(p) for p in parts):
            raise ValueError(f"'{name}' contains quotes, semicolons, spaces or control characters.")
        col = (column or "").strip() or None
        if col is not None:
            if len(parts) != 3:
                raise ValueError(f"A column needs a catalog.schema.table name, got '{name}'.")
            if _UNSAFE_COLUMN_NAME.search(col):
                raise ValueError(f"Column '{col}' contains backticks or control characters.")
        return cls(
            catalog=parts[0],
            schema_name=parts[1] if len(parts) > 1 else None,
            object_name=parts[2] if len(parts) > 2 else None,
            column_name=col,
        )

    @classmethod
    def from_fqn_key(cls, fqn_key: str) -> "ObjectKey":
        relation, _, column = fqn_key.partition(COLUMN_SEPARATOR)
        return cls.parse(relation, column or None)

    @property
    def level(self) -> Level:
        if self.column_name:
            return "column"
        if self.object_name:
            return "relation"
        if self.schema_name:
            return "schema"
        return "catalog"

    @property
    def name(self) -> str:
        """The catalog, schema or relation name (without the column)."""
        return ".".join(p for p in (self.catalog, self.schema_name, self.object_name) if p)

    @property
    def display(self) -> str:
        return f"{self.name}.{self.column_name}" if self.column_name else self.name

    @property
    def fqn_key(self) -> str:
        base = self.name.lower()
        return f"{base}{COLUMN_SEPARATOR}{self.column_name.lower()}" if self.column_name else base

    def parent(self) -> Optional["ObjectKey"]:
        if self.column_name:
            return ObjectKey(catalog=self.catalog, schema_name=self.schema_name, object_name=self.object_name)
        if self.object_name:
            return ObjectKey(catalog=self.catalog, schema_name=self.schema_name)
        if self.schema_name:
            return ObjectKey(catalog=self.catalog)
        return None

    def ancestors(self) -> List["ObjectKey"]:
        """Nearest first: the relation (for a column), then the schema, then the catalog."""
        found: List[ObjectKey] = []
        node = self.parent()
        while node is not None:
            found.append(node)
            node = node.parent()
        return found

    def relation_key(self) -> "ObjectKey":
        """The object itself, or its relation for a column (the unit the reconciler queues)."""
        return self.parent() if self.column_name else self  # type: ignore[return-value]


class Authorship(BaseModel):
    set_by: Optional[str] = None
    set_by_type: Optional[ActorType] = None
    source: Optional[ChangeSource] = None
    set_at: Optional[datetime] = None
    change_set_id: Optional[str] = None


class TagAssignment(Authorship):
    key: str
    # None = key-only tag.
    value: Optional[str] = None
    version: int = 1


class CommentState(Authorship):
    # None = no description.
    value: Optional[str] = None
    # 0 = never set.
    version: int = 0


class DesiredState(BaseModel):
    """One ``metadata_objects`` row with its tags."""

    id: str
    key: ObjectKey
    fqn_key: str
    display_name: str
    securable_type: SecurableType
    environment: Optional[str] = None
    tags: Dict[str, Optional[str]] = Field(default_factory=dict)
    tag_details: List[TagAssignment] = Field(default_factory=list)
    comment: CommentState = Field(default_factory=CommentState)
    status: ObjectStatus = "active"
    object_state: ObjectState = "pending"
    orphaned_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    created_by_type: Optional[ActorType] = None
    updated_at: Optional[datetime] = None
    last_applied_at: Optional[datetime] = None
    last_apply_status: Optional[ApplyStatus] = None
    last_apply_error: Optional[str] = None
    last_observed_at: Optional[datetime] = None
    drift_detected_at: Optional[datetime] = None
    desired_hash: Optional[str] = None


class ObservedState(BaseModel):
    """One ``metadata_observed`` row: what UC held at ``observed_at``."""

    fqn_key: str
    key: ObjectKey
    securable_type: SecurableType
    table_type: Optional[str] = None
    data_type: Optional[str] = None
    ordinal_position: Optional[int] = None
    owner: Optional[str] = None
    comment: Optional[str] = None
    tags: Dict[str, Optional[str]] = Field(default_factory=dict)
    present: bool = True
    created: Optional[datetime] = None
    last_altered: Optional[datetime] = None
    observed_at: Optional[datetime] = None
    missing_since: Optional[datetime] = None


# --- Actors and principals ------------------------------------------------------------

ChangeOpType = Literal["set_tags", "unset_tags", "replace_tags", "set_comment", "clear_comment"]
CHANGE_OP_TYPES: tuple = ("set_tags", "unset_tags", "replace_tags", "set_comment", "clear_comment")


class PrincipalScope(BaseModel):
    """What a registered machine caller may edit (``metadata_principals``)."""

    application_id: str
    display_name: str = ""
    description: Optional[str] = None
    enabled: bool = True
    # Name globs: "sales_*", "sales_dev.bronze", "*.bronze.*". Empty = nothing.
    scopes: List[str] = Field(default_factory=list)
    # Tag-key globs. Empty = any key the key policies allow.
    allowed_keys: List[str] = Field(default_factory=list)
    # Empty = all operations.
    allowed_operations: List[ChangeOpType] = Field(default_factory=list)
    # Empty = all environments.
    environments: List[str] = Field(default_factory=list)
    max_objects_per_change: Optional[int] = None
    can_skip_approval: bool = False


class PrincipalIn(PrincipalScope):
    pass


class PrincipalOut(PrincipalScope):
    last_seen_at: Optional[datetime] = None
    last_seen_headers: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None


class Actor(BaseModel):
    """Who is making a change, resolved once per request by ``authz.actor_from_user``."""

    # Email for a user, application id for a service principal, "system" for the app.
    id: str
    actor_type: ActorType = "user"
    display_name: Optional[str] = None
    email: Optional[str] = None
    roles: List[str] = Field(default_factory=list)
    groups: List[str] = Field(default_factory=list)
    # Platform Admin or one of METADATA_EDITOR_ROLES.
    is_admin: bool = False
    principal: Optional[PrincipalScope] = None


class WhoAmI(BaseModel):
    actor: Actor
    # user | service_principal (registered) | unregistered_service_principal
    recognized_as: Literal["user", "service_principal", "unregistered_service_principal"]
    # Names of the identity headers present on this request (never their values for tokens).
    headers_seen: List[str] = Field(default_factory=list)
    identifiers: Dict[str, Optional[str]] = Field(default_factory=dict)
    # Whether the caller may open the Metadata Manager at all (browse_scope isn't none).
    can_browse: bool = True
    # full = every object; owned = only objects the caller owns; none = nothing.
    browse_scope: Literal["full", "owned", "none"] = "full"
    message: Optional[str] = None


# --- Changes -------------------------------------------------------------------------


class ChangeOp(BaseModel):
    """One requested operation on one object.

    * ``set_tags`` merges ``tags`` into the object's tags;
    * ``unset_tags`` removes ``keys``;
    * ``replace_tags`` makes ``tags`` the full set (exempt keys are untouched);
    * ``set_comment`` sets ``comment``; ``clear_comment`` removes it.

    ``expected_versions`` maps a tag key to the version the caller last saw (0 = the
    tag must not exist yet); ``expected_comment_version`` does the same for the
    description. A mismatch fails the whole change with 409.

    ``match`` replaces ``name`` with a glob (``sales_dev.*.*``; ``*`` and ``?`` within
    one part), which ``expand.expand_request`` turns into one op per known object at
    that level, before anything else sees the request. ``column`` is then a glob too.
    """

    op: ChangeOpType
    name: str = ""
    column: Optional[str] = None
    tags: Dict[str, Optional[str]] = Field(default_factory=dict)
    keys: List[str] = Field(default_factory=list)
    comment: Optional[str] = None
    expected_versions: Dict[str, int] = Field(default_factory=dict)
    expected_comment_version: Optional[int] = None
    # Type hint for an object that doesn't exist yet (a pending row); the snapshot wins once it does.
    securable_type: Optional[SecurableType] = None
    match: Optional[str] = None
    # With ``match``: only objects of these types (empty = every type at the glob's level).
    match_types: List[SecurableType] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def _normalize(cls, v: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
        return normalize_tags(v)

    @model_validator(mode="after")
    def _name_or_match(self) -> "ChangeOp":
        has_name, has_match = bool((self.name or "").strip()), bool((self.match or "").strip())
        if has_name == has_match:
            raise ValueError("Give either 'name' or 'match' (a name pattern such as sales_dev.*.*), not both.")
        return self


ApprovalRouting = Literal["request", "split", "reject"]


class ChangeRequest(BaseModel):
    ops: List[ChangeOp] = Field(min_length=1)
    reason: str = ""
    # Also apply each op to the same object in these environments (D5).
    environments: List[str] = Field(default_factory=list)
    # request = the whole change goes to approval if any item needs it; split = commit the
    # direct items and file the rest; reject = refuse with the reasons.
    on_approval_needed: ApprovalRouting = "request"
    idempotency_key: Optional[str] = None


class PromoteRequest(BaseModel):
    """Copy a lower environment's desired tags and description to other environments."""

    name: str
    column: Optional[str] = None
    # Include the object's columns (for a relation).
    include_columns: bool = True
    to_environments: List[str] = Field(min_length=1)
    include_tags: bool = True
    include_comment: bool = True
    reason: str = ""
    on_approval_needed: ApprovalRouting = "request"
    idempotency_key: Optional[str] = None


class RevertRequest(BaseModel):
    reason: str = ""
    on_approval_needed: ApprovalRouting = "request"


ItemAction = Literal["set", "unset", "comment_set", "comment_clear"]


class ChangeItem(BaseModel):
    """One resolved tag or description edit on one object."""

    fqn_key: str
    name: str
    column: Optional[str] = None
    securable_type: SecurableType
    environment: Optional[str] = None
    field: Literal["tag", "comment"]
    key: Optional[str] = None
    action: ItemAction
    before: Optional[str] = None
    before_exists: bool = False
    # 0 when the tag or description didn't exist.
    before_version: int = 0
    after: Optional[str] = None
    # Whether the object already has a desired row; False = this change creates it.
    object_exists: bool = True
    object_state: ObjectState = "exists"
    # The change creates the row of an object Unity Catalog already has, and UC already holds this value:
    # it's recorded so the first desired state is complete, and nothing in UC changes.
    unchanged_in_uc: bool = False
    # The environment this item was copied from, for a cross-environment copy or promote.
    copied_from: Optional[str] = None
    # The caller can't browse this object, so ``before`` and its version were left out of what it got back.
    before_hidden: bool = False


class Finding(BaseModel):
    code: str
    severity: Severity
    message: str
    fqn_key: Optional[str] = None
    key: Optional[str] = None


class AuthzDecision(BaseModel):
    # May be written without approval.
    allowed: bool = False
    needs_approval: bool = False
    # May be filed as a request even though not allowed directly.
    can_request: bool = True
    # admin | owner | uc_owner | principal | none
    via: Literal["admin", "owner", "uc_owner", "principal", "none"] = "none"
    reasons: List[str] = Field(default_factory=list)
    sensitive_reasons: List[str] = Field(default_factory=list)
    # Groups whose members approve this item (owner groups, else the governance group).
    approver_groups: List[str] = Field(default_factory=list)
    # The object whose owner tag decided authority (the object itself or an ancestor).
    owner_source: Optional[str] = None


class ItemDecision(BaseModel):
    item: ChangeItem
    decision: AuthzDecision
    findings: List[Finding] = Field(default_factory=list)


class ChangePreview(BaseModel):
    items: List[ItemDecision] = Field(default_factory=list)
    # Change-level findings (limits, reason length, unknown environment, ...).
    findings: List[Finding] = Field(default_factory=list)
    # A deny finding anywhere: nothing can be committed or requested.
    blocked: bool = False
    direct_count: int = 0
    approval_count: int = 0
    denied_count: int = 0
    # Ops that resolved to no change (already true).
    noop_count: int = 0
    # Of direct_count: items that are ``unchanged_in_uc``.
    kept_count: int = 0
    needs_approval: bool = False
    approver_groups: List[str] = Field(default_factory=list)
    environments: List[str] = Field(default_factory=list)
    object_count: int = 0
    # sha256 over the resolved items; record refuses a change whose digest moved.
    digest: str = ""
    report_markdown: str = ""
    risk: Optional[Dict[str, Any]] = None


class ChangeEvent(BaseModel):
    id: int
    change_set_id: str
    object_id: str
    fqn_key: str
    name: Optional[str] = None
    action: str
    key: Optional[str] = None
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    actor: Optional[str] = None
    actor_type: Optional[ActorType] = None
    source: str
    created_at: Optional[datetime] = None


class ChangeSet(BaseModel):
    id: str
    actor: str
    actor_type: ActorType = "user"
    source: ChangeSource
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None
    request_id: Optional[str] = None
    reverts_change_set_id: Optional[str] = None
    reverted_by: Optional[str] = None
    summary: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    events: List[ChangeEvent] = Field(default_factory=list)


class ChangeSetPage(BaseModel):
    items: List[ChangeSet]
    total: int
    page: int = 1
    page_size: int = 50


ChangeStatus = Literal["committed", "pending_approval", "partial", "noop", "rejected"]


class ChangeResult(BaseModel):
    # committed = written; pending_approval = filed as a request; partial = split: some
    # written, the rest filed; noop = nothing to change; rejected = refused.
    status: ChangeStatus
    change_set: Optional[ChangeSet] = None
    request_id: Optional[str] = None
    preview: ChangePreview
    message: str = ""
    # True when an earlier result was returned for a repeated idempotency key.
    replayed: bool = False


class VersionConflictItem(BaseModel):
    fqn_key: str
    name: str
    # Tag key, or None for the description.
    key: Optional[str] = None
    expected_version: int
    current_version: int
    current_value: Optional[str] = None


# --- Reconciler ----------------------------------------------------------------------

PlanOperation = Literal["set", "unset", "comment"]
PlanStatus = Literal["planned", "applied", "noop", "failed", "skipped"]
RunTrigger = Literal["queue", "sweep", "manual", "new_object", "notify"]
RunStatus = Literal["running", "completed", "partial", "failed", "paused", "skipped"]


class ObjectDiff(BaseModel):
    """Desired vs observed for one object, after exempt keys are set aside."""

    fqn_key: str
    name: str
    securable_type: SecurableType
    observed_present: bool = False
    to_set: Dict[str, Optional[str]] = Field(default_factory=dict)
    to_unset: List[str] = Field(default_factory=list)
    comment_changed: bool = False
    comment_before: Optional[str] = None
    comment_after: Optional[str] = None

    @property
    def in_sync(self) -> bool:
        return not self.to_set and not self.to_unset and not self.comment_changed


class PlanItem(BaseModel):
    fqn_key: str
    object_id: Optional[str] = None
    name: str
    column: Optional[str] = None
    securable_type: SecurableType
    operation: PlanOperation
    tags: Dict[str, Optional[str]] = Field(default_factory=dict)
    keys: List[str] = Field(default_factory=list)
    comment: Optional[str] = None
    sql: str
    # Removed tags and cleared descriptions count toward the circuit breaker.
    removals: int = 0


class PlanOutcome(PlanItem):
    status: PlanStatus = "planned"
    detail: Optional[str] = None


class ReconcileResult(BaseModel):
    run_id: str
    trigger: RunTrigger
    mode: ReconcileMode
    status: RunStatus
    actor: Optional[str] = None
    objects_checked: int = 0
    objects_drifted: int = 0
    statements_planned: int = 0
    statements_applied: int = 0
    statements_failed: int = 0
    removals_planned: int = 0
    skipped_catalogs: List[str] = Field(default_factory=list)
    # catalog -> why it was skipped (read failure, outside this deployment's list, ...)
    skipped_reasons: Dict[str, str] = Field(default_factory=dict)
    # Catalogs this run couldn't read from Unity Catalog, so none of their objects were checked.
    unread_catalogs: List[str] = Field(default_factory=list)
    # Whole catalogs; otherwise only named objects, so its counts say nothing about the rest.
    full_check: bool = False
    paused_reason: Optional[str] = None
    error: Optional[str] = None
    items: List[PlanOutcome] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ReconcileRunPage(BaseModel):
    items: List[ReconcileResult]
    total: int
    page: int = 1
    page_size: int = 50


class ReconcileNotify(BaseModel):
    """A pipeline telling us objects were just created or changed."""

    names: List[str] = Field(min_length=1, max_length=1000)


class ReconcileAccepted(BaseModel):
    enqueued: List[str]
    rejected: List[Dict[str, str]] = Field(default_factory=list)


class RunRequest(BaseModel):
    # sweep = refresh the whole snapshot and reconcile everything; plan = sweep without writing.
    kind: Literal["sweep", "plan", "queue"] = "sweep"
    catalogs: List[str] = Field(default_factory=list)
    # Resume a run paused by the circuit breaker, applying its removals anyway.
    override_circuit_breaker: bool = False


# --- Search and detail ---------------------------------------------------------------

TagFilterOp = Literal["eq", "exists", "missing"]


class TagFilter(BaseModel):
    key: str
    op: TagFilterOp = "eq"
    value: Optional[str] = None

    @classmethod
    def parse(cls, text: str) -> "TagFilter":
        """``key=value``, ``key`` (has the key) or ``!key`` (lacks the key)."""
        raw = (text or "").strip()
        if raw.startswith("!"):
            return cls(key=raw[1:].strip(), op="missing")
        if "=" in raw:
            k, _, v = raw.partition("=")
            return cls(key=k.strip(), op="eq", value=normalize_value(v.strip()))
        return cls(key=raw, op="exists")


SortField = Literal["name", "updated_at", "last_applied_at", "drift_detected_at", "catalog", "securable_type"]


class SearchQuery(BaseModel):
    q: str = ""
    catalogs: List[str] = Field(default_factory=list)
    schemas: List[str] = Field(default_factory=list)
    securable_types: List[SecurableType] = Field(default_factory=list)
    environments: List[str] = Field(default_factory=list)
    object_states: List[ObjectState] = Field(default_factory=list)
    tags: List[TagFilter] = Field(default_factory=list)
    # Only objects tagged ``dataset`` or ``data_set`` with this value.
    dataset: Optional[str] = None
    # None = either; True = only objects whose desired and observed state differ.
    drift: Optional[bool] = None
    include_columns: bool = False
    # Only objects the caller may edit directly.
    editable_only: bool = False
    # Include observed objects with no desired row (shown as unmanaged).
    include_unmanaged: bool = True
    sort: SortField = "name"
    order: Literal["asc", "desc"] = "asc"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)


class ObjectSummary(BaseModel):
    # None for an observed object with no desired row.
    id: Optional[str] = None
    fqn_key: str
    name: str
    column: Optional[str] = None
    securable_type: SecurableType
    catalog: str
    schema_name: Optional[str] = None
    object_name: Optional[str] = None
    environment: Optional[str] = None
    managed: bool = True
    tags: Dict[str, Optional[str]] = Field(default_factory=dict)
    # Desired tag key -> version, for expected_versions; None for an unmanaged object.
    tag_versions: Optional[Dict[str, int]] = None
    comment: Optional[str] = None
    comment_version: Optional[int] = None
    observed_tags: Optional[Dict[str, Optional[str]]] = None
    observed_comment: Optional[str] = None
    object_state: Optional[ObjectState] = None
    drift: bool = False
    drift_detected_at: Optional[datetime] = None
    last_apply_status: Optional[ApplyStatus] = None
    last_applied_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    data_type: Optional[str] = None
    # None when the caller's permissions weren't evaluated.
    editable: Optional[bool] = None


class Facet(BaseModel):
    value: str
    count: int


class DatasetSummary(BaseModel):
    """The tables and views tagged ``dataset`` (or ``data_set``) with one value."""
    name: str
    table_count: int
    catalogs: List[str] = Field(default_factory=list)
    # Of table_count: managed objects whose Unity Catalog state differs from the desired one.
    out_of_sync_count: int = 0


class SearchResult(BaseModel):
    items: List[ObjectSummary]
    total: int
    page: int = 1
    page_size: int = 50
    # catalog, securable_type, environment, object_state, tag_key
    facets: Dict[str, List[Facet]] = Field(default_factory=dict)
    took_ms: Optional[int] = None


class DriftEntry(BaseModel):
    field: Literal["tag", "comment"]
    key: Optional[str] = None
    desired: Optional[str] = None
    observed: Optional[str] = None
    # missing = desired but not in UC; extra = in UC but not desired; changed = different value
    kind: Literal["missing", "extra", "changed"]
    exempt: bool = False


class ObjectDetail(BaseModel):
    key: ObjectKey
    fqn_key: str
    desired: Optional[DesiredState] = None
    observed: Optional[ObservedState] = None
    drift: List[DriftEntry] = Field(default_factory=list)
    # A relation's columns: desired rows joined with observed columns, in column order.
    columns: List[ObjectSummary] = Field(default_factory=list)
    owner_groups: List[str] = Field(default_factory=list)
    owner_source: Optional[str] = None
    editable: bool = False
    edit_reasons: List[str] = Field(default_factory=list)
    # The same object in other environments: {"prd": "sales_prd.bronze.orders"}.
    counterparts: Dict[str, str] = Field(default_factory=dict)
    history: List[ChangeEvent] = Field(default_factory=list)
    recent_plan: List[PlanOutcome] = Field(default_factory=list)


class KeyPolicy(BaseModel):
    key: str
    applies_to: List[str] = Field(default_factory=list)
    allowed_values: List[str] = Field(default_factory=list)
    pattern: Optional[str] = None
    editor_groups: List[str] = Field(default_factory=list)
    sensitive: bool = False
    required: bool = False
    description: Optional[str] = None


class KeyInfo(BaseModel):
    key: str
    count: int = 0
    values: List[Facet] = Field(default_factory=list)
    policy: Optional[KeyPolicy] = None
    exempt: bool = False
    governed: bool = False


class EnvironmentInfo(BaseModel):
    key: str
    catalog_pattern: Optional[str] = None
    catalogs: List[str] = Field(default_factory=list)
    approval: Literal["sensitive", "all"] = "sensitive"


class MetadataConfig(BaseModel):
    reconcile_mode: ReconcileMode
    owner_tag_key: str
    exempt_keys: List[str]
    key_mode: Literal["open", "known_only"] = "open"
    key_policies: List[KeyPolicy] = Field(default_factory=list)
    environments: List[EnvironmentInfo] = Field(default_factory=list)
    max_objects_per_change: int = 5000
    min_reason_length: int = 0
    min_justification_length: int = 10
    new_object_policy: Literal["adopt", "ignore"] = "adopt"
    last_sweep_at: Optional[datetime] = None
    # Catalogs the latest full check couldn't read; their lists may be incomplete.
    unread_catalogs: List[str] = Field(default_factory=list)
    # Same catalogs with a short reason the page can show (warehouse timeout, no BROWSE, ...).
    unread_reasons: Dict[str, str] = Field(default_factory=dict)
    circuit_breaker_tripped: bool = False
    # Catalog names or patterns this deployment manages; empty = every catalog, but plan only.
    managed_catalogs: List[str] = Field(default_factory=list)
    # Set when enforce is configured but can't run (no catalog list), so the UI can say why.
    enforce_blocked_reason: Optional[str] = None


# --- Adopt and import ----------------------------------------------------------------


class AdoptRequest(BaseModel):
    """Make the observed state of objects their desired state (cutover, D11)."""

    catalogs: List[str] = Field(default_factory=list)
    names: List[str] = Field(default_factory=list)
    # False = skip objects that already have a desired row.
    overwrite: bool = False
    include_columns: bool = True
    reason: str = "Adopt current Unity Catalog state"


class ImportRow(BaseModel):
    name: str
    column: Optional[str] = None
    tags: Optional[Dict[str, Optional[str]]] = None
    # None = leave the description as it is; "" = clear it.
    comment: Optional[str] = None


class ImportRequest(BaseModel):
    rows: List[ImportRow] = Field(default_factory=list)
    # CSV text with a header row: name,column,comment,tags (tags is a JSON object).
    csv: Optional[str] = None
    # merge = set_tags; replace = replace_tags.
    mode: Literal["merge", "replace"] = "merge"
    reason: str = ""
