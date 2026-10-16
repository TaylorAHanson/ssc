"""Access Rules DTOs shared by the services, the API, the workflow tools and the UI.

A *rule* is one workbook row (packed ``;`` cells and all). An *atom* is one
principal x one privilege on one securable, the unit policy and diffs reason
about. A *change set* is an atomic batch of row edits from one source. An
*access change* is the request-side shape: grant or revoke privileges for a
principal on a securable, which the ledger turns into row edits.
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Plane = Literal["uc", "uc_metastore", "workspace"]
Severity = Literal["deny", "warn"]
# Which write path a policy evaluation is for. Imports are warn-only.
WritePath = Literal["request", "admin_edit"]

CANONICAL_FIELDS = (
    "environment_value",
    "securable_type",
    "catalog",
    "schema_name",
    "object_name",
    "workspace_name",
    "owner",
    "principal_type",
    "principal_name",
    "privilege",
)


class RuleCells(BaseModel):
    """The modelled cells of a workbook row, as written (``;``-packed where the sheet packs them)."""

    environment_value: str = ""
    securable_type: str = ""
    catalog: str = ""
    schema_name: str = ""
    object_name: str = ""
    workspace_name: str = ""
    owner: str = ""
    principal_type: str = ""
    principal_name: str = ""
    privilege: str = ""


class RuleRow(RuleCells):
    """A rule independent of the database (used by cells.py, policy.py and the planners)."""

    id: Optional[str] = None
    sheet: str
    plane: Plane
    environment: Optional[str] = None
    position: int = 0
    # Unmodelled columns, keyed by column index (as a string) in the sheet layout.
    extra: Dict[str, str] = Field(default_factory=dict)
    status: str = "active"
    version: int = 1


class Rule(RuleRow):
    """A rule as the API returns it."""

    source: Optional[str] = None
    source_ref: Optional[str] = None
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    # Open rbac_access_change requests whose plan touches this row.
    pending_request_ids: List[str] = Field(default_factory=list)


class RulesPage(BaseModel):
    rules: List[Rule]
    total: int


class Finding(BaseModel):
    severity: Severity
    # Stable machine code, e.g. hard_denied_privilege, principal_type_not_allowed,
    # system_group, naming_rule, unknown_privilege, deprecated_privilege,
    # self_service_disabled, write_privilege_disabled, justification_too_short,
    # duplicate_row, unknown_header, inflight_overlap, broad_grant, mixed_planes.
    code: str
    message: str
    sheet: Optional[str] = None
    rule_id: Optional[str] = None
    # Index into the submitted changes (admin edit) or items (access change).
    change_index: Optional[int] = None
    # Imported row number within its sheet (1-based, excluding the header).
    row_number: Optional[int] = None
    field: Optional[str] = None
    principal: Optional[str] = None
    privilege: Optional[str] = None


# --- Direct edits -------------------------------------------------------------


class RowChange(BaseModel):
    op: Literal["create", "update", "delete"]
    # update / delete: the row and the version the editor saw (409 on mismatch).
    rule_id: Optional[str] = None
    version: Optional[int] = None
    # create: the sheet to append to.
    sheet: Optional[str] = None
    # create: all cells (missing = ""); update: only the cells that change.
    cells: Dict[str, str] = Field(default_factory=dict)
    extra: Optional[Dict[str, str]] = None


class ChangeSetIn(BaseModel):
    changes: List[RowChange] = Field(min_length=1)
    reason: str = ""


class RowDiff(BaseModel):
    op: Literal["create", "update", "delete", "restore"]
    rule_id: Optional[str] = None
    sheet: str
    plane: Plane
    environment: Optional[str] = None
    # Snapshots: canonical cells + extra (+ position/version on existing rows).
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None


class ChangeSetPreview(BaseModel):
    diffs: List[RowDiff]
    findings: List[Finding]
    # Any deny finding.
    blocked: bool
    # A touched environment has the two-person rule on: commit files a request.
    two_person_required: bool = False


class ChangeSetResult(BaseModel):
    change_set_id: Optional[str] = None
    # Set instead of change_set_id when the two-person rule turned the edit into a request.
    request_id: Optional[str] = None
    diffs: List[RowDiff] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)


class ChangeSetSummary(BaseModel):
    id: str
    source: str
    source_ref: Optional[str] = None
    actor: Optional[str] = None
    reason: Optional[str] = None
    summary: Dict[str, Any] = Field(default_factory=dict)
    reverted_by: Optional[str] = None
    created_at: Optional[datetime] = None


class ChangeSetDetail(ChangeSetSummary):
    diffs: List[RowDiff] = Field(default_factory=list)


class HistoryPage(BaseModel):
    change_sets: List[ChangeSetSummary]
    total: int


class RevertIn(BaseModel):
    reason: str = ""


# --- Access-change requests ---------------------------------------------------


class AccessItem(BaseModel):
    """Grant or revoke ``privileges`` for one principal on one securable."""

    action: Literal["grant", "revoke"] = "grant"
    # Environment key or alias. Required for uc and workspace; optional for metastore.
    environment: Optional[str] = None
    # Workbook spelling, lowercase: schema, view, table, catalog, connection,
    # workspace, workspace entitlement, ...
    securable_type: str
    catalog: str = ""
    schema_name: str = ""
    object_name: str = ""
    workspace_name: str = ""
    # Workbook spelling: "group" or "service principal".
    principal_type: str = "group"
    principal_name: str
    privileges: List[str] = Field(min_length=1)
    # Derived from securable_type when omitted.
    plane: Optional[Plane] = None


class AccessChangeIn(BaseModel):
    items: List[AccessItem] = Field(min_length=1)
    justification: str = ""


class ItemOutcome(BaseModel):
    index: int
    item: AccessItem
    # change = edits the ledger; noop = already true (or already absent); blocked = a deny finding.
    status: Literal["change", "noop", "blocked"]
    reason: Optional[str] = None


class AccessProposal(BaseModel):
    """One type's part of an access change: what a single rbac_access_change request will do."""

    plane: Plane
    approver_group: Optional[str] = None
    # No group configured for this type: Platform Admins approve.
    approver_fallback: bool = False
    # Normalized items for this plane, including added prerequisites. This is the
    # request's ``items`` parameter, and the plan apply intersects with.
    items: List[AccessItem]
    added_prerequisites: List[AccessItem] = Field(default_factory=list)
    outcomes: List[ItemOutcome] = Field(default_factory=list)
    diffs: List[RowDiff] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    blocked: bool = False
    report_markdown: str = ""


class AccessPreview(BaseModel):
    # Shared by the linked requests when the change spans more than one type.
    group_ref: Optional[str] = None
    proposals: List[AccessProposal]


class ApplyResult(BaseModel):
    change_set_id: Optional[str] = None
    applied: List[RowDiff] = Field(default_factory=list)
    # Items that became no-ops, or fell outside the approved plan, since approval.
    skipped: List[ItemOutcome] = Field(default_factory=list)
    # The request's change set already exists (idempotent re-run).
    already_applied: bool = False


class AccessMatch(BaseModel):
    rule_id: str
    sheet: str
    plane: Plane
    environment: Optional[str] = None
    securable_type: str
    catalog: str = ""
    schema_name: str = ""
    object_name: str = ""
    workspace_name: str = ""
    principal_type: str
    principal_name: str
    privileges: List[str]
    # direct = on the securable asked about; parent = inherited from a catalog or schema row.
    via: Literal["direct", "parent"] = "direct"


class FindAccessResult(BaseModel):
    matches: List[AccessMatch]
    truncated: bool = False


# --- Permissions and sheets ---------------------------------------------------


class PlanePermission(BaseModel):
    plane: Plane
    label: str
    approver_group: Optional[str] = None
    # unset | found | not_found | unknown (lookup failed)
    group_status: Literal["unset", "found", "not_found", "unknown"] = "unset"
    can_edit: bool = False


class RbacPermissions(BaseModel):
    can_view: bool
    can_import: bool
    can_reset: bool
    can_export: bool
    planes: List[PlanePermission]
    min_reason_length: int = 0
    max_changes_per_commit: int = 0


class SheetColumn(BaseModel):
    header: str
    field: Optional[str] = None


class SheetInfo(BaseModel):
    name: str
    plane: Plane
    environment: Optional[str] = None
    columns: List[SheetColumn]
    row_count: int = 0
    export_target: str
    export_path: str = ""
    editable: bool = False


# --- Import -------------------------------------------------------------------


class ImportSheetSummary(BaseModel):
    sheet: str
    plane: Plane
    environment: Optional[str] = None
    rows: int
    columns: List[SheetColumn]
    unknown_headers: List[str] = Field(default_factory=list)
    duplicates: int = 0
    warnings: int = 0


class ImportPreview(BaseModel):
    import_id: str
    filename: Optional[str] = None
    file_sha256: str
    sheets: List[ImportSheetSummary]
    # Workbook tabs that aren't managed sheets (e.g. a scratch tab).
    skipped_sheets: List[str] = Field(default_factory=list)
    # Managed sheets the workbook doesn't have.
    missing_sheets: List[str] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    findings_total: int = 0
    existing_rules: int = 0
    # initial when the ledger is empty, reset otherwise.
    mode: Literal["initial", "reset"]
    reset_allowed: bool = False
    in_flight_requests: int = 0
    # The exact text the admin must type to confirm a reset.
    confirmation_phrase: Optional[str] = None


class ImportRunIn(BaseModel):
    preview_id: str
    mode: Literal["initial", "reset"]
    confirmation: str = ""


class ImportResult(BaseModel):
    import_id: str
    change_set_id: Optional[str] = None
    created: int = 0
    deleted: int = 0
    sheets: List[ImportSheetSummary] = Field(default_factory=list)


class ImportSummary(BaseModel):
    id: str
    filename: Optional[str] = None
    mode: Optional[str] = None
    status: str
    actor: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    change_set_id: Optional[str] = None
    error: Optional[str] = None


# --- Export -------------------------------------------------------------------


class ExportFile(BaseModel):
    id: str
    sheet: str
    path: Optional[str] = None
    sha256: Optional[str] = None
    row_count: int = 0
    size_bytes: int = 0
    status: Literal["written", "skipped", "failed"]
    error: Optional[str] = None
    downloadable: bool = False


class ExportRun(BaseModel):
    id: str
    trigger: Literal["schedule", "manual", "on_change"]
    actor: Optional[str] = None
    status: Literal["running", "completed", "partial", "skipped", "failed"]
    watermark: int = 0
    change_summary: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    files: List[ExportFile] = Field(default_factory=list)


class ExportTarget(BaseModel):
    # An environment key, or "metastore".
    key: str
    label: str
    path: str = ""
    sheets: List[str]
    configured: bool


class ExportStatus(BaseModel):
    runs: List[ExportRun]
    cron: str = ""
    next_run_at: Optional[datetime] = None
    targets: List[ExportTarget]
    # The ledger changed since the last successful export.
    pending_changes: bool = False


class ExportRunIn(BaseModel):
    # Export even when nothing changed since the last successful export.
    force: bool = False


class PathCheckIn(BaseModel):
    # An environment key, or "metastore".
    target: str


class PathCheckResult(BaseModel):
    target: str
    path: str = ""
    ok: bool
    message: str


class CompareDifference(BaseModel):
    # bom | line_endings | trailing_newline | encoding | header | quoting | rows
    kind: str
    detail: str


class CompareResult(BaseModel):
    sheet: str
    identical: bool
    ours_sha256: str
    theirs_sha256: str
    differences: List[CompareDifference] = Field(default_factory=list)
    # Data rows present on one side only (capped), as cell lists.
    only_in_ours: List[List[str]] = Field(default_factory=list)
    only_in_theirs: List[List[str]] = Field(default_factory=list)
