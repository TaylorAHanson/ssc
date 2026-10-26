"""Metadata Manager tools for the chat agent and the in-app MCP server (ADR-0006 D6).

Thin wrappers over the same change service as the REST API. The caller is rebuilt
from the identity the ToolExecutor injects, so a registered service principal
calling over MCP gets its principal scope and a person gets their own rights.
"""
import logging
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from app.services.metadata.types import ApprovalRouting, ChangeOp
from app.tools.mcp import tool

logger = logging.getLogger(__name__)

_MAX_TOOL_PAGE_SIZE = 100


def _actor(db, kwargs: Dict[str, Any]):
    from app.models.user import User
    from app.services.approval_scope import parse_csv
    from app.services.metadata import authz

    email = kwargs.get("_user_email")
    if not email:
        return None
    user = User(
        id=email, email=email, full_name=email,
        roles=parse_csv(kwargs.get("_user_roles")), entitlements=parse_csv(kwargs.get("_user_entitlements")),
        actor_type=kwargs.get("_actor_type") or "user", application_id=kwargs.get("_principal_id"),
    )
    return authz.actor_from_user(db, user)


def _source(kwargs: Dict[str, Any]) -> str:
    return "mcp" if kwargs.get("_channel") == "mcp" else "agent"


def _no_identity() -> Dict[str, Any]:
    return {"ok": False, "error": "I couldn't tell who is asking, so I can't read or change metadata for you."}


def _no_browse() -> Dict[str, Any]:
    return {"ok": False, "status": "forbidden", "status_code": 403,
            "error": "You don't have access to the Metadata Manager: it is limited to governance and data owners, "
                     "and you don't own any objects in it, so I can't search or read metadata for you. Ask a "
                     "Platform Admin to add one of your roles to the Metadata Manager's browse roles."}


def _error(exc: Exception) -> Dict[str, Any]:
    from app.services.metadata.errors import ChangeBlocked, MetadataError, VersionConflict

    if isinstance(exc, VersionConflict):
        return {"ok": False, "error": exc.message, "status": "conflict",
                "conflicts": [c.model_dump(mode="json") for c in exc.conflicts],
                "next_step": "Read the objects again with get_metadata, show the user the current values and "
                             "ask whether to re-apply their change."}
    if isinstance(exc, ChangeBlocked):
        return {"ok": False, "error": exc.message, "status": "blocked",
                "report_markdown": exc.preview.report_markdown,
                "findings": [f.model_dump(mode="json") for f in exc.preview.findings]}
    if isinstance(exc, MetadataError):
        return {"ok": False, "error": exc.message, "status_code": exc.status_code}
    raise exc


def _with_session(kwargs: Dict[str, Any], work, *, browse: bool = False) -> Dict[str, Any]:
    """Run ``work(db, actor, scope)`` with the caller's browse scope, which limits what it returns.

    ``browse`` also refuses a caller whose scope sees nothing, as the REST reads do.
    """
    from app.db.session import get_lakebase_session
    from app.services.metadata import authz
    from app.services.metadata.errors import MetadataError

    db = get_lakebase_session()
    try:
        actor = _actor(db, kwargs)
        if actor is None:
            return _no_identity()
        scope = authz.browse_scope(db, actor)
        if browse and scope.level == "none":
            logger.info("Metadata: refused %s (%s) a %s call: nothing to browse",
                        actor.id, actor.actor_type, _source(kwargs))
            return _no_browse()
        try:
            return work(db, actor, scope)
        except MetadataError as exc:
            db.rollback()
            return _error(exc)
    finally:
        db.close()


# --- search_metadata ---------------------------------------------------------------------


_TAG_FILTER_HELP = "'key=value', 'key' (has the key) or '!key' (lacks the key)"


class SearchMetadataInput(BaseModel):
    query: str = Field(default="", description="Free text matched against names, descriptions and tag values.")
    catalog: str = Field(default="", description="Only this catalog.")
    tag: str = Field(default="", description=f"A tag filter: {_TAG_FILTER_HELP}.")
    securable_type: Literal["", "catalog", "schema", "table", "view", "materialized_view", "streaming_table",
                            "metric_view", "column"] = Field(default="", description="Only this kind of object.")
    editable_only: bool = Field(default=False, description="Only objects the caller may change directly.")
    limit: int = Field(default=25, ge=1, le=_MAX_TOOL_PAGE_SIZE)
    schema_name: str = Field(default="", description="Only this schema, as catalog.schema.")
    tags: List[str] = Field(default_factory=list, description=f"More tag filters, all must match: {_TAG_FILTER_HELP}.")
    drift: Optional[bool] = Field(default=None, description="True = only objects whose Unity Catalog state differs.")
    include_columns: bool = Field(default=False, description="Also return matching columns.")
    page: int = Field(default=1, ge=1)


@tool(
    name="search_metadata",
    description=(
        "Search the Metadata Manager's desired state for Unity Catalog tags and descriptions. Reads the app's "
        "own tables (never Unity Catalog), so it is fast. Returns matching objects with their desired tags, "
        "description, whether Unity Catalog has drifted, whether the caller may edit them, plus facets. Use it "
        "to find objects before get_metadata or preview_metadata_change."
    ),
    args_schema=SearchMetadataInput,
    feature_flag="metadata_v2",
    friendly_label="Searching metadata",
    friendly_completion_label="Searched metadata",
    external=True,
    requires_identity=True,
    allow_service_principals=True,
    is_lookup=True,
)
def search_metadata(**kwargs) -> Dict[str, Any]:
    from app.services.metadata import search
    from app.services.metadata.types import SearchQuery, TagFilter

    args = {k: v for k, v in kwargs.items() if not k.startswith("_")}
    filters = [args.get("tag") or "", *(args.get("tags") or [])]
    try:
        query = SearchQuery(
            q=args.get("query") or "",
            catalogs=[args["catalog"]] if args.get("catalog") else [],
            schemas=[args["schema_name"]] if args.get("schema_name") else [],
            securable_types=[args["securable_type"]] if args.get("securable_type") else [],
            tags=[TagFilter.parse(t) for t in filters if str(t).strip()],
            drift=args.get("drift"), include_columns=bool(args.get("include_columns")),
            editable_only=bool(args.get("editable_only")), page=args.get("page") or 1,
            page_size=min(args.get("limit") or 25, _MAX_TOOL_PAGE_SIZE),
        )
    except (ValidationError, ValueError) as exc:
        return {"ok": False, "error": f"Invalid search: {exc}"}

    def work(db, actor, scope) -> Dict[str, Any]:
        result = search.search(db, query, actor, browse=scope)
        return {"ok": True, **result.model_dump(mode="json")}

    return _with_session(kwargs, work, browse=True)


# --- get_metadata ------------------------------------------------------------------------


class GetMetadataInput(BaseModel):
    name: str = Field(..., description="catalog, catalog.schema or catalog.schema.table.")
    column: Optional[str] = Field(default=None, description="A column of the table, for column-level metadata.")


@tool(
    name="get_metadata",
    description=(
        "One object's full metadata: desired tags (with who set each and its version), description, what Unity "
        "Catalog currently holds, the per-key drift, its columns, owner groups and where they come from, whether "
        "the caller may edit it and why, the same object in other environments, and recent history. Versions "
        "are what preview_metadata_change uses to detect concurrent edits."
    ),
    args_schema=GetMetadataInput,
    feature_flag="metadata_v2",
    friendly_label="Reading metadata",
    friendly_completion_label="Read metadata",
    external=True,
    requires_identity=True,
    allow_service_principals=True,
    is_lookup=True,
)
def get_metadata(name: str, column: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    from app.services.metadata import detail

    def work(db, actor, scope) -> Dict[str, Any]:
        found = detail.object_detail(db, actor, name, column, browse=scope)
        return {"ok": True, **found.model_dump(mode="json")}

    return _with_session(kwargs, work, browse=True)


# --- preview / submit --------------------------------------------------------------------


class MetadataChangeInput(BaseModel):
    changes: List[ChangeOp] = Field(
        ..., min_length=1,
        description=(
            "One entry per object: op ('set_tags' merges tags, 'unset_tags' removes keys, 'replace_tags' makes tags "
            "the full set, 'set_comment' / 'clear_comment' for the description), name (catalog[.schema[.table]]), "
            "optional column, tags as {key: value} (null value = key-only tag), keys for unset_tags, comment. "
            "The object doesn't have to exist yet: its tags are applied when it is created."
        ),
    )
    reason: str = Field(
        default="", description="The user's own reason, in their words. Leave empty until they give one; never write it.",
    )
    environments: List[str] = Field(
        default_factory=list,
        description="Also apply each change to the same object in these environments (e.g. 'prd').",
    )


class SubmitMetadataChangeInput(MetadataChangeInput):
    on_approval_needed: ApprovalRouting = Field(
        default="request",
        description=(
            "When some items need approval: 'request' files the whole change for approval, 'split' writes what "
            "the caller may change directly and files the rest, 'reject' refuses."
        ),
    )
    idempotency_key: Optional[str] = Field(
        default=None, description="A caller-chosen key; resubmitting with the same key returns the first result.",
    )


def _change_request(kwargs: Dict[str, Any], *, submit: bool):
    from app.services.metadata.types import ChangeRequest

    payload = {"ops": kwargs.get("changes") or [], "reason": kwargs.get("reason") or "",
               "environments": kwargs.get("environments") or []}
    if submit:
        payload["on_approval_needed"] = kwargs.get("on_approval_needed") or "request"
        payload["idempotency_key"] = kwargs.get("idempotency_key")
    return ChangeRequest.model_validate(payload)


def _next_step(preview) -> str:
    if preview.blocked:
        return "A policy blocks this change. Show the report and the deny findings; don't submit it."
    if preview.direct_count - preview.kept_count + preview.approval_count == 0:
        return "Nothing would change. Tell the user their objects already have this metadata."
    if preview.needs_approval:
        groups = ", ".join(preview.approver_groups) or "the governance approvers"
        return (f"Show report_markdown. {preview.approval_count} item(s) need approval from {groups}. Ask for an "
                "explicit yes (and the user's reason if they haven't given one), then call submit_metadata_change "
                "with the same changes; it files the approval request itself.")
    return ("Show report_markdown, ask for an explicit yes, then call submit_metadata_change with the same changes. "
            "It is written immediately and applied to Unity Catalog by the reconciler.")


@tool(
    name="preview_metadata_change",
    description=(
        "Dry-run a change to Unity Catalog tags or descriptions without writing anything. Resolves each op "
        "against the desired state, checks the key policies and who may change what, and returns per-item "
        "decisions (direct, needs approval, denied), approver groups, findings, report_markdown and next_step. "
        "For many objects at once, give an op 'match' (a name pattern such as sales_dev.bronze.* or "
        "sales_*.*.*, * and ? within one part; {sales}.bronze.* means the sales catalog in every environment) "
        "instead of 'name'; with 'column' it matches that column name "
        "(a pattern too) on every matching table. Findings say how many objects each pattern matched. "
        "Always preview, show the report and get an explicit yes before submit_metadata_change."
    ),
    args_schema=MetadataChangeInput,
    feature_flag="metadata_v2",
    friendly_label="Checking the metadata change",
    friendly_completion_label="Checked the metadata change",
    external=True,
    requires_identity=True,
    allow_service_principals=True,
)
def preview_metadata_change(**kwargs) -> Dict[str, Any]:
    from app.services.metadata import expand

    try:
        ask = _change_request(kwargs, submit=False)
    except ValidationError as exc:
        return {"ok": False, "error": f"Invalid change: {exc.errors(include_url=False)}"}

    def work(db, actor, scope) -> Dict[str, Any]:
        preview = expand.preview(db, ask, actor, browse=scope)
        return {"ok": not preview.blocked, **preview.model_dump(mode="json"), "next_step": _next_step(preview)}

    return _with_session(kwargs, work, browse=True)


@tool(
    name="submit_metadata_change",
    description=(
        "Submit a previewed change to Unity Catalog tags or descriptions. Items the caller may change directly "
        "are written to the desired state at once and applied by the reconciler; items that need approval are "
        "filed as one metadata change request routed to the object's owner groups (and governance for "
        "sensitive tags). Returns status (committed, pending_approval, partial, noop, rejected), the change set, "
        "the request id and a message. Only call it after the user said yes to the preview."
    ),
    args_schema=SubmitMetadataChangeInput,
    feature_flag="metadata_v2",
    friendly_label="Submitting the metadata change",
    friendly_completion_label="Submitted the metadata change",
    external=True,
    side_effect_class="app_write",
    requires_identity=True,
    allow_service_principals=True,
)
def submit_metadata_change(**kwargs) -> Dict[str, Any]:
    from app.services.metadata import expand

    try:
        ask = _change_request(kwargs, submit=True)
    except ValidationError as exc:
        return {"ok": False, "error": f"Invalid change: {exc.errors(include_url=False)}"}

    def work(db, actor, scope) -> Dict[str, Any]:
        result = expand.submit(db, ask, actor, source=_source(kwargs), browse=scope)
        out = result.model_dump(mode="json", exclude={"preview"})
        out["report_markdown"] = result.preview.report_markdown
        return {"ok": result.status != "rejected", **out}

    return _with_session(kwargs, work)
