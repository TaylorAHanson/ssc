"""
Governance Tag Management API.

Find tables, views and datasets; read their current Unity Catalog tags (and
their columns' tags); run rich policy & risk & hygiene checks; and submit tag
changes either via GitOps (opening a GitHub PR) or in Local Execution Mode
(applying changes directly to Unity Catalog when GitHub Actions / networking is
blocked). The logic lives in ``app.services.tag_change``; this module is HTTP.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_any_role
from app.core.config import settings
from app.db.request import RequestModel
from app.db.session import get_db
from app.models.request import RequestType
from app.services.tag_change import datasets as tag_datasets
from app.services.tag_change import engine
from app.services.tag_change import search as tag_search
from app.state_machines.facts import get_latest_fact
from app.workflows.tag_governed import fetch_governed_tags, search_governed_keys
from app.workflows.tag_plan import _normalize_fqn, fetch_columns, fetch_live_state, validate_target

router = APIRouter()
logger = logging.getLogger(__name__)

_ADMIN_ROLES = ["platform_admin", "governance_admin"]

# Re-exported for callers that still import them from here.
RESERVED_TAG_KEYS = engine.RESERVED_TAG_KEYS
SUGGESTED_TAG_KEYS = engine.SUGGESTED_TAG_KEYS


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TagModeResponse(BaseModel):
    local_mode: bool
    repo: Optional[str] = None
    base_branch: Optional[str] = None
    ledger_table: Optional[str] = None
    environment: str
    columns_supported: bool = False


class TagDataset(BaseModel):
    dataset_id: str
    catalog: Optional[str] = None
    schema_name: Optional[str] = None


class TableTags(BaseModel):
    table: str
    tags: Dict[str, Optional[str]]
    object_type: Optional[str] = None
    exists: bool = True


class DatasetTablesResponse(BaseModel):
    dataset_id: str
    tables: List[TableTags]
    suggested_keys: List[str]
    error: Optional[str] = None


class TagSearchObject(BaseModel):
    fqn: str
    object_type: Optional[str] = None
    owner: Optional[str] = None
    tag_keys: List[str] = []
    in_cache: bool = True


class TagSearchResponse(BaseModel):
    query: str
    datasets: List[TagDataset]
    objects: List[TagSearchObject]
    total_objects: int
    truncated: bool
    filters: List[str] = []


class ObjectsRequest(BaseModel):
    tables: List[str]


class ObjectsResponse(BaseModel):
    tables: List[TableTags]
    suggested_keys: List[str]


class ColumnTags(BaseModel):
    column: str
    data_type: str = ""
    tags: Dict[str, Optional[str]]


class TableColumnsResponse(BaseModel):
    table: str
    columns: List[ColumnTags]
    error: Optional[str] = None


class GovernedTagInfo(BaseModel):
    key: str
    governed: bool
    allowed_values: List[str] = []
    description: str = ""


class TableDesiredTags(BaseModel):
    table: str
    desired_tags: Dict[str, str]
    column: Optional[str] = None


class TagChangeCreate(BaseModel):
    dataset_id: Optional[str] = None
    dataset_name: Optional[str] = None
    tables: List[TableDesiredTags]
    pr_title: Optional[str] = None


class TagPreviewResponse(BaseModel):
    valid: bool
    policy_violations: List[str] = []
    policy_warnings: List[str] = []
    plan: Dict[str, Any]
    risk: Dict[str, Any]
    lint: Dict[str, Any]
    agent_review: Dict[str, Any]


class TagChangeResponse(BaseModel):
    id: str
    title: str
    dataset_id: Optional[str] = None
    status: str
    execution_mode: str = "gitops"  # "local" | "gitops"
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    requested_by: Optional[str] = None
    table_count: int = 0  # objects (tables, views or columns) changed
    applied_count: int = 0
    noop_count: int = 0
    failed_count: int = 0
    created_at: datetime
    updated_at: datetime


class TagChangeDetailResponse(BaseModel):
    id: str
    title: str
    dataset_id: Optional[str] = None
    status: str
    execution_mode: str = "gitops"
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    table_count: int = 0
    applied_count: int = 0
    noop_count: int = 0
    failed_count: int = 0
    plan: Optional[Dict[str, Any]] = None
    risk: Optional[Dict[str, Any]] = None
    lint: Optional[Dict[str, Any]] = None
    agent_review: Optional[Dict[str, Any]] = None
    outcomes: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_provider():
    return engine.get_provider()


def _targets(payload: TagChangeCreate) -> List[engine.TagTarget]:
    return [
        engine.TagTarget(table=t.table.strip(), column=(t.column or None), desired_tags=t.desired_tags)
        for t in payload.tables
    ]


def _table_tags(provider, table_names: List[str]) -> List[TableTags]:
    live_state = fetch_live_state(provider, table_names)
    tables: List[TableTags] = []
    for name in table_names:
        obj_state = live_state.get(_normalize_fqn(name))
        if obj_state and (obj_state.exists or obj_state.tags):
            tags: Dict[str, Optional[str]] = dict(obj_state.tags)
        else:
            tags = engine.read_table_tags(provider, name)
        tables.append(
            TableTags(
                table=name,
                tags=tags,
                object_type=obj_state.object_type if obj_state and obj_state.exists else None,
                exists=bool(obj_state and obj_state.exists) or bool(tags),
            )
        )
    return tables


def _pr_link(db: Session, request_id: str):
    pr_fact = get_latest_fact(db, request_id, "pr_created")
    data = pr_fact.event_data if pr_fact and pr_fact.event_data else {}
    return data.get("pr_url"), data.get("pr_number")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/mode", response_model=TagModeResponse)
def get_tag_manager_mode(
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Return the current tag management mode (Local Mode vs GitOps Mode)."""
    local = bool(settings.GOVERNANCE_TAGS_LOCAL_MODE)
    return TagModeResponse(
        local_mode=local,
        repo=settings.GOVERNANCE_TAGS_REPO or None,
        base_branch=settings.GOVERNANCE_TAGS_BASE_BRANCH or None,
        ledger_table=settings.GOVERNANCE_TAGS_LEDGER_TABLE or None,
        environment=settings.ENVIRONMENT or "dev",
        columns_supported=local,
    )


@router.get("/datasets", response_model=List[TagDataset])
def list_tag_datasets(
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """List datasets available for tag management (active data contracts)."""
    return [
        TagDataset(dataset_id=d.dataset_id, catalog=d.catalog, schema_name=d.schema_name)
        for d in tag_datasets.list_datasets(db)
    ]


@router.get("/datasets/{dataset_id:path}/tables", response_model=DatasetTablesResponse)
def get_dataset_tables(
    dataset_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Return the member tables of a dataset with their current editable tags."""
    try:
        provider = _get_provider()
        table_names = tag_datasets.discover_dataset_tables(provider, dataset_id, db=db)
        return DatasetTablesResponse(
            dataset_id=dataset_id,
            tables=_table_tags(provider, table_names) if table_names else [],
            suggested_keys=SUGGESTED_TAG_KEYS,
        )
    except Exception as e:
        logger.error(f"Failed to load tables for dataset {dataset_id}: {e}")
        return DatasetTablesResponse(
            dataset_id=dataset_id,
            tables=[],
            suggested_keys=SUGGESTED_TAG_KEYS,
            error=str(e),
        )


@router.get("/search", response_model=TagSearchResponse)
def search_tag_targets(
    q: str = Query("", description="Name words, globs (main.sales.*), key=value, key=*, !key"),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Quick search across governed datasets, tables and views."""
    provider = None
    if not tag_search.parse_query(q).is_empty:
        try:
            provider = _get_provider()
        except Exception as e:  # noqa: BLE001 - name search still works from the cache
            logger.warning(f"Tag search running without Unity Catalog access: {e}")
    result = tag_search.search(db, provider, q, limit=limit)
    return TagSearchResponse(
        query=q,
        datasets=[
            TagDataset(dataset_id=d.dataset_id, catalog=d.catalog, schema_name=d.schema_name)
            for d in result.datasets
        ],
        objects=[
            TagSearchObject(
                fqn=o.fqn, object_type=o.object_type, owner=o.owner, tag_keys=o.tag_keys, in_cache=o.in_cache
            )
            for o in result.objects
        ],
        total_objects=result.total_objects,
        truncated=result.truncated,
        filters=result.filters,
    )


@router.post("/objects", response_model=ObjectsResponse)
def get_object_tags(
    payload: ObjectsRequest,
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Current editable tags for specific tables and views."""
    names = list(dict.fromkeys(t.strip() for t in payload.tables if t and t.strip()))
    if len(names) > engine.MAX_TARGETS:
        raise HTTPException(status_code=400, detail=f"At most {engine.MAX_TARGETS} objects can be loaded at once.")
    for name in names:
        try:
            validate_target(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if not names:
        return ObjectsResponse(tables=[], suggested_keys=SUGGESTED_TAG_KEYS)
    return ObjectsResponse(tables=_table_tags(_get_provider(), names), suggested_keys=SUGGESTED_TAG_KEYS)


@router.get("/columns", response_model=TableColumnsResponse)
def get_table_columns(
    table: str = Query(..., description="Three-part table or view name"),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """A table or view's columns with their current editable tags."""
    try:
        validate_target(table)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        columns = fetch_columns(_get_provider(), table.strip())
    except Exception as e:
        logger.warning(f"Failed to load columns for {table}: {e}")
        return TableColumnsResponse(table=table, columns=[], error=str(e))
    return TableColumnsResponse(
        table=table,
        columns=[ColumnTags(column=c["column"], data_type=c["data_type"], tags=c["tags"]) for c in columns],
    )


@router.get("/governed/keys", response_model=List[GovernedTagInfo])
def list_governed_tag_keys(
    q: str = Query("", description="Text the key contains"),
    limit: int = Query(30, ge=1, le=100),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Unity Catalog governed tag keys matching ``q``, for the editor's key typeahead."""
    try:
        tags = search_governed_keys(_get_provider(), q, limit=limit)
    except Exception as e:  # noqa: BLE001 - suggestions only
        logger.warning(f"Could not list governed tags: {e}")
        return []
    return [
        GovernedTagInfo(key=t.key, governed=True, allowed_values=t.allowed_values, description=t.description)
        for t in tags
    ]


@router.get("/governed", response_model=List[GovernedTagInfo])
def get_governed_tags(
    keys: List[str] = Query(..., description="Tag keys to look up"),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Unity Catalog tag policies for these keys: whether each is governed and its allowed values."""
    wanted = [k.strip() for k in keys if k and k.strip()][:50]
    try:
        governed = fetch_governed_tags(_get_provider(), wanted)
    except Exception as e:  # noqa: BLE001 - suggestions only; the preview re-checks
        logger.warning(f"Could not look up governed tags: {e}")
        return [GovernedTagInfo(key=k, governed=False) for k in wanted]
    out: List[GovernedTagInfo] = []
    for k in wanted:
        tag = governed.get(k)
        out.append(
            GovernedTagInfo(
                key=k,
                governed=tag is not None,
                allowed_values=tag.allowed_values if tag else [],
                description=tag.description if tag else "",
            )
        )
    return out


@router.post("/preview", response_model=TagPreviewResponse)
async def preview_tag_change(
    payload: TagChangeCreate,
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """
    Run full pre-execution checks: policy validation, plan diff generation,
    hygiene/typo linting against the live catalog vocabulary, deterministic risk scoring,
    and advisory AI agent review.
    """
    targets = _targets(payload)
    try:
        engine.validate_targets(targets)
    except engine.TagChangeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    provider = _get_provider()
    policy = await engine.load_tag_policy_async()
    evaluation = engine.evaluate(
        provider, policy, targets, dataset_values=[payload.dataset_id, payload.dataset_name]
    )
    agent_review = await engine.review(
        evaluation, engine.scope_label(targets, payload.dataset_id, payload.dataset_name)
    )
    return TagPreviewResponse(
        valid=evaluation.valid,
        policy_violations=evaluation.violations,
        policy_warnings=evaluation.warnings,
        plan=evaluation.plan_dict(),
        risk=evaluation.risk.to_dict(),
        lint=evaluation.lint_dict(),
        agent_review=agent_review.to_dict(),
    )


@router.post("/changes", response_model=TagChangeResponse)
def create_tag_change(
    payload: TagChangeCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """
    Submit tag changes. In Local Execution Mode, applies directly to Unity Catalog
    and records the result. In GitOps Mode, opens a tracked GitHub pull request.
    """
    targets = _targets(payload)
    try:
        engine.validate_targets(targets)
        provider = _get_provider()
        evaluation = engine.evaluate(
            provider,
            engine.load_tag_policy(),
            targets,
            dataset_values=[payload.dataset_id, payload.dataset_name],
        )
        submitted = engine.submit(
            db,
            provider,
            evaluation,
            scope=engine.scope_label(targets, payload.dataset_id, payload.dataset_name),
            dataset_id=payload.dataset_id,
            actor_email=current_user.email,
            actor_name=current_user.full_name,
            pr_title=payload.pr_title,
        )
    except engine.TagChangeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    request = submitted.request
    apply_res = submitted.apply_result
    return TagChangeResponse(
        id=request.id,
        title=request.title,
        dataset_id=payload.dataset_id,
        status=request.status,
        execution_mode=submitted.execution_mode,
        requested_by=request.requester_email,
        table_count=submitted.change_count,
        applied_count=apply_res.applied_count if apply_res else 0,
        noop_count=apply_res.noop_count if apply_res else 0,
        failed_count=apply_res.failed_count if apply_res else 0,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


@router.get("/changes", response_model=List[TagChangeResponse])
def list_tag_changes(
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """List tag-change requests with their execution mode, status, and PR link."""
    requests = (
        db.query(RequestModel)
        .filter(RequestModel.type == RequestType.TAG_CHANGE.value)
        .order_by(RequestModel.created_at.desc())
        .all()
    )

    results: List[TagChangeResponse] = []
    for r in requests:
        ctx = r.state_context or {}
        pr_url, pr_number = _pr_link(db, r.id)
        results.append(
            TagChangeResponse(
                id=r.id,
                title=r.title,
                dataset_id=ctx.get("dataset_id"),
                status=r.status,
                execution_mode=ctx.get("execution_mode", "gitops"),
                pr_url=pr_url,
                pr_number=pr_number,
                requested_by=r.requester_email,
                table_count=len(ctx.get("changes") or []),
                applied_count=int(ctx.get("statements_applied") or 0),
                noop_count=int(ctx.get("statements_noop") or 0),
                failed_count=int(ctx.get("statements_failed") or 0),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
        )
    return results


@router.get("/changes/{change_id}", response_model=TagChangeDetailResponse)
def get_tag_change_detail(
    change_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Get full details, checks, plan diffs, and execution outcomes for a tag change."""
    request = db.query(RequestModel).filter(RequestModel.id == change_id).first()
    if not request:
        raise HTTPException(status_code=404, detail=f"Tag change request '{change_id}' not found.")

    ctx = request.state_context or {}
    pr_url, pr_number = _pr_link(db, request.id)
    apply_result = ctx.get("apply_result") or {}

    return TagChangeDetailResponse(
        id=request.id,
        title=request.title,
        dataset_id=ctx.get("dataset_id"),
        status=request.status,
        execution_mode=ctx.get("execution_mode", "gitops"),
        pr_url=pr_url,
        pr_number=pr_number,
        table_count=len(ctx.get("changes") or []),
        applied_count=int(ctx.get("statements_applied") or 0),
        noop_count=int(ctx.get("statements_noop") or 0),
        failed_count=int(ctx.get("statements_failed") or 0),
        plan=ctx.get("plan"),
        risk=ctx.get("risk"),
        lint=ctx.get("lint"),
        agent_review=ctx.get("agent_review"),
        outcomes=apply_result.get("outcomes"),
        error=ctx.get("error"),
        created_at=request.created_at,
        updated_at=request.updated_at,
    )
