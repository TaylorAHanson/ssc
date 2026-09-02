"""
Governance Tag Management API.

Read current Unity Catalog tags per dataset/table, run rich policy & risk & hygiene checks,
and submit tag changes either via GitOps (opening a GitHub PR) or in Local Execution Mode
(applying changes directly to Unity Catalog when GitHub Actions / networking is blocked).
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_any_role
from app.core.config import settings
from app.db.data_contract import DataContractModel
from app.db.request import RequestModel
from app.db.session import get_db
from app.models.request import RequestType
from app.state_machines.facts import add_fact, get_latest_fact
from app.workflows.tag_apply import apply_tag_plan
from app.workflows.tag_lint import run_lint_checks
from app.workflows.tag_plan import (
    DATASET_KEY,
    build_tag_plan,
    fetch_live_state,
    fetch_tag_vocabulary,
)
from app.workflows.tag_policy import TagPolicy, get_default_policy, load_policy
from app.workflows.tag_review import request_agent_review
from app.workflows.tag_risk import calculate_risk_score
from app.workflows.tag_sql import build_tag_sql

router = APIRouter()
logger = logging.getLogger(__name__)

# Tag keys the governance UI must never touch. ``system.*`` tags (e.g.
# ``system.certification_status``) are owned by the Enforcement Sentinel and are
# excluded from both display-for-edit and the SET/UNSET diff.
RESERVED_TAG_PREFIXES = ("system.",)

# Governance keys surfaced as suggestions in the editor (free-form keys allowed).
SUGGESTED_TAG_KEYS = [
    "access_group",
    "approver_group",
    "data_owner",
    "dataset",
    "reliability_window",
    "classification",
]


def _is_reserved(key: str) -> bool:
    return any(key.startswith(p) for p in RESERVED_TAG_PREFIXES)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TagModeResponse(BaseModel):
    local_mode: bool
    repo: Optional[str] = None
    base_branch: Optional[str] = None
    ledger_table: Optional[str] = None
    environment: str


class TagDataset(BaseModel):
    dataset_id: str
    catalog: Optional[str] = None
    schema_name: Optional[str] = None


class TableTags(BaseModel):
    table: str
    tags: Dict[str, Optional[str]]


class DatasetTablesResponse(BaseModel):
    dataset_id: str
    tables: List[TableTags]
    suggested_keys: List[str]
    error: Optional[str] = None


class TableDesiredTags(BaseModel):
    table: str
    desired_tags: Dict[str, str]


class TagChangeCreate(BaseModel):
    dataset_id: str
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
    table_count: int = 0
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
# Databricks helpers & Policy
# ---------------------------------------------------------------------------

def _get_provider():
    from app.core.workspaces import get_governance_uc_provider

    return get_governance_uc_provider()


def _extract_contract_info(contract: DataContractModel) -> Tuple[Optional[str], Optional[str], List[str]]:
    """Extract default catalog, schema, and declared member tables from a Data Contract YAML."""
    if not contract or not contract.yaml_content:
        return None, None, []
    try:
        data = yaml.safe_load(contract.yaml_content) or {}
    except Exception as e:
        logger.warning(f"Could not parse contract YAML for {contract.dataset_id}: {e}")
        return None, None, []

    servers = data.get("servers", [])
    default_catalog = servers[0].get("catalog", "") if servers else ""
    default_schema = servers[0].get("schema", "") if servers else ""

    tables: List[str] = []
    schemas = data.get("schema", [])
    for s in schemas:
        physical_table = s.get("physicalName") or s.get("name")
        if not physical_table:
            continue
        table_catalog = s.get("catalog") or default_catalog
        table_schema = s.get("schema") or default_schema
        if "." in physical_table:
            parts = physical_table.split(".")
            if len(parts) == 3:
                tables.append(physical_table)
            elif len(parts) == 2 and table_catalog:
                tables.append(f"{table_catalog}.{physical_table}")
            elif table_catalog and table_schema:
                tables.append(f"{table_catalog}.{table_schema}.{physical_table}")
        elif table_catalog and table_schema:
            tables.append(f"{table_catalog}.{table_schema}.{physical_table}")

    return (default_catalog or None), (default_schema or None), tables


def _discover_dataset_tables(provider, dataset_id: str, db: Optional[Session] = None) -> List[str]:
    """
    Find the member tables of a dataset.
    Discovers tables declared in active data contracts (ODCS) as well as tables
    tagged with dataset='<dataset_id>' in Unity Catalog.
    """
    from app.core.workspaces import catalogs_to_scan

    discovered: Set[str] = set()

    # 1. Look up tables declared in DataContractModel (ODCS)
    if db:
        contract = (
            db.query(DataContractModel)
            .filter(DataContractModel.dataset_id == dataset_id, DataContractModel.is_active == True)  # noqa: E712
            .order_by(DataContractModel.version.desc())
            .first()
        )
        if contract:
            _, _, contract_tables = _extract_contract_info(contract)
            for t in contract_tables:
                discovered.add(t)

    # 2. Query live Unity Catalog for any tables tagged with dataset='<dataset_id>'
    if provider:
        catalog_names, _missing = catalogs_to_scan(provider.client)
        safe_id = dataset_id.replace("'", "''")
        for catalog_name in catalog_names:
            query = (
                f"SELECT catalog_name, schema_name, table_name "
                f"FROM {catalog_name}.information_schema.table_tags "
                f"WHERE (tag_name = 'dataset' OR tag_name = 'data_set') AND tag_value = '{safe_id}'"
            )
            try:
                response = provider.client.statement_execution.execute_statement(
                    statement=query,
                    warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                    wait_timeout="30s",
                )
                if response.result and response.result.data_array:
                    for row in response.result.data_array:
                        discovered.add(f"{row[0]}.{row[1]}.{row[2]}")
            except Exception as e:
                logger.warning(f"Could not query information_schema for catalog {catalog_name}: {e}")

    return sorted(discovered)


def _get_table_tags(provider, full_name: str) -> Dict[str, Optional[str]]:
    """Read current UC tag key->value pairs for a table (excluding reserved keys)."""
    tags: Dict[str, Optional[str]] = {}
    try:
        uc_tags = provider.client.entity_tag_assignments.list(
            entity_type="tables", entity_name=full_name
        )
        for t in uc_tags:
            key = getattr(t, "tag_key", None)
            if key and not _is_reserved(key):
                tags[key] = getattr(t, "tag_value", None)
    except Exception as e:
        logger.warning(f"Could not list tags for {full_name}: {e}")
    return tags


def _load_tag_policy() -> TagPolicy:
    """Load the governance policy, falling back safely to the canonical default policy."""
    if settings.GOVERNANCE_TAGS_LOCAL_MODE or not settings.GOVERNANCE_TAGS_REPO:
        return get_default_policy()
    try:
        from app.workflows.tools import _get_github_provider

        policy = asyncio.run(
            load_policy(
                _get_github_provider(),
                repo=settings.GOVERNANCE_TAGS_REPO,
                ref=settings.GOVERNANCE_TAGS_BASE_BRANCH or "dev",
            )
        )
        return policy or get_default_policy()
    except Exception as e:
        logger.warning(f"Could not load tag policy from GitHub: {e}")
        return get_default_policy()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/mode", response_model=TagModeResponse)
def get_tag_manager_mode(
    current_user=Depends(require_any_role(["platform_admin", "governance_admin"])),
):
    """Return the current tag management mode (Local Mode vs GitOps Mode)."""
    return TagModeResponse(
        local_mode=bool(settings.GOVERNANCE_TAGS_LOCAL_MODE),
        repo=settings.GOVERNANCE_TAGS_REPO or None,
        base_branch=settings.GOVERNANCE_TAGS_BASE_BRANCH or None,
        ledger_table=settings.GOVERNANCE_TAGS_LEDGER_TABLE or None,
        environment=settings.ENVIRONMENT or "dev",
    )


@router.get("/datasets", response_model=List[TagDataset])
def list_tag_datasets(
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(["platform_admin", "governance_admin"])),
):
    """List datasets available for tag management (active data contracts)."""
    contracts = (
        db.query(DataContractModel)
        .filter(DataContractModel.is_active == True)  # noqa: E712
        .order_by(DataContractModel.version.desc())
        .all()
    )
    results: List[TagDataset] = []
    seen: Set[str] = set()
    for c in contracts:
        if c.dataset_id in seen:
            continue
        seen.add(c.dataset_id)
        catalog, schema_name, _ = _extract_contract_info(c)
        if not catalog or not schema_name:
            parts = (c.dataset_id or "").split(".")
            if len(parts) >= 2:
                catalog = parts[0]
                schema_name = parts[1]
            elif len(parts) == 1 and not catalog:
                catalog = None
                schema_name = None
        results.append(
            TagDataset(
                dataset_id=c.dataset_id,
                catalog=catalog,
                schema_name=schema_name,
            )
        )
    results.sort(key=lambda d: d.dataset_id.lower())
    return results


@router.get("/datasets/{dataset_id:path}/tables", response_model=DatasetTablesResponse)
def get_dataset_tables(
    dataset_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(["platform_admin", "governance_admin"])),
):
    """Return the member tables of a dataset with their current editable tags."""
    try:
        provider = _get_provider()
        table_names = _discover_dataset_tables(provider, dataset_id, db=db)
        tables = [
            TableTags(table=name, tags=_get_table_tags(provider, name))
            for name in table_names
        ]
        return DatasetTablesResponse(
            dataset_id=dataset_id,
            tables=tables,
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


@router.post("/preview", response_model=TagPreviewResponse)
async def preview_tag_change(
    payload: TagChangeCreate,
    current_user=Depends(require_any_role(["platform_admin", "governance_admin"])),
):
    """
    Run full pre-execution checks: policy validation, plan diff generation,
    hygiene/typo linting against the live catalog vocabulary, deterministic risk scoring,
    and advisory AI agent review.
    """
    if not payload.tables:
        raise HTTPException(status_code=400, detail="No tables specified in payload.")

    for t in payload.tables:
        parts = (t.table or "").strip().split(".")
        if len(parts) != 3 or not all(p.strip() for p in parts):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid table identifier '{t.table}'. Must be a 3-part name: catalog.schema.table",
            )

    provider = _get_provider()
    policy = _load_tag_policy()

    # 1. Fetch live object state & tags
    table_names = [t.table for t in payload.tables]
    live_state = fetch_live_state(provider, table_names)

    # 2. Build Plan & Diffs
    tables_payload = [{"table": t.table, "desired_tags": t.desired_tags} for t in payload.tables]
    plan = build_tag_plan(tables_payload, live_state)

    # 3. Policy validation check
    changes: List[dict] = []
    resulting_counts: Dict[str, int] = {}
    for diff in plan.diffs.values():
        if diff.changed_keys:
            set_tags = {k: diff.after[k] for k in diff.after if diff.before.get(k) != diff.after[k]}
            unset_tags = diff.removed_keys
            changes.append({"table": diff.table, "set": set_tags, "unset": unset_tags})
            resulting_counts[diff.table] = len(diff.after)

    violations = policy.check(changes, resulting_counts) if changes else []

    # 4. Fetch tag vocabulary & dataset members
    keys_of_interest = set(SUGGESTED_TAG_KEYS)
    for diff in plan.diffs.values():
        keys_of_interest.update(diff.changed_keys)
    vocab = fetch_tag_vocabulary(
        provider=provider,
        table_names=table_names,
        keys_of_interest=sorted(keys_of_interest),
        dataset_values=[payload.dataset_id, payload.dataset_name],
        dataset_key=DATASET_KEY,
    )

    # 5. Hygiene / Typo lint checks
    lint_findings = run_lint_checks(plan, vocab, policy)

    # 6. Deterministic Risk scoring
    risk_report = calculate_risk_score(
        plan=plan,
        environment=settings.ENVIRONMENT or "dev",
        findings=lint_findings,
        vocabulary=vocab,
        policy=policy,
    )

    # 7. Advisory AI Agent Review
    dataset_display = payload.dataset_name or payload.dataset_id
    agent_review = await request_agent_review(
        dataset_name=dataset_display,
        plan=plan,
        risk_report=risk_report,
        lint_findings=lint_findings,
    )

    return TagPreviewResponse(
        valid=len(violations) == 0,
        policy_violations=violations,
        policy_warnings=[],
        plan=plan.to_dict(),
        risk=risk_report.to_dict(),
        lint={"findings": [f.to_dict() for f in lint_findings]},
        agent_review=agent_review.to_dict(),
    )


@router.post("/changes", response_model=TagChangeResponse)
async def create_tag_change(
    payload: TagChangeCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(["platform_admin", "governance_admin"])),
):
    """
    Submit tag changes. In Local Execution Mode, applies directly to Unity Catalog
    and records the result. In GitOps Mode, opens a tracked GitHub pull request.
    """
    if not payload.tables:
        raise HTTPException(status_code=400, detail="No tables specified in payload.")

    for t in payload.tables:
        parts = (t.table or "").strip().split(".")
        if len(parts) != 3 or not all(p.strip() for p in parts):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid table identifier '{t.table}'. Must be a 3-part name: catalog.schema.table",
            )

    provider = _get_provider()
    policy = _load_tag_policy()

    table_names = [t.table for t in payload.tables]
    live_state = fetch_live_state(provider, table_names)
    tables_payload = [{"table": t.table, "desired_tags": t.desired_tags} for t in payload.tables]
    plan = build_tag_plan(tables_payload, live_state)

    changes: List[dict] = []
    resulting_counts: Dict[str, int] = {}
    for diff in plan.diffs.values():
        if diff.changed_keys:
            set_tags = {k: diff.after[k] for k in diff.after if diff.before.get(k) != diff.after[k]}
            unset_tags = diff.removed_keys
            changes.append({"table": diff.table, "set": set_tags, "unset": unset_tags})
            resulting_counts[diff.table] = len(diff.after)

    if not changes and not plan.actionable:
        raise HTTPException(status_code=400, detail="No tag changes detected.")

    violations = policy.check(changes, resulting_counts)
    if violations:
        raise HTTPException(
            status_code=400,
            detail="This change would be rejected by the tag policy:\n- " + "\n- ".join(violations),
        )

    dataset_name = payload.dataset_name or payload.dataset_id
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    # -----------------------------------------------------------------------
    # LOCAL MODE EXECUTION
    # -----------------------------------------------------------------------
    if settings.GOVERNANCE_TAGS_LOCAL_MODE:
        logger.info(f"[{request_id}] Executing tag changes locally for '{dataset_name}' ({len(changes)} table(s))")

        # Compute risk and lint for provenance metadata
        keys_of_interest = set(SUGGESTED_TAG_KEYS)
        for diff in plan.diffs.values():
            keys_of_interest.update(diff.changed_keys)
        vocab = fetch_tag_vocabulary(
            provider=provider,
            table_names=table_names,
            keys_of_interest=sorted(keys_of_interest),
            dataset_values=[payload.dataset_id, payload.dataset_name],
        )
        lint_findings = run_lint_checks(plan, vocab, policy)
        risk_report = calculate_risk_score(
            plan=plan,
            environment=settings.ENVIRONMENT or "dev",
            findings=lint_findings,
            vocabulary=vocab,
            policy=policy,
        )

        # Apply statements directly to Unity Catalog
        apply_res = apply_tag_plan(
            provider=provider,
            plan=plan,
            request_id=request_id,
            actor=current_user.email,
            environment=settings.ENVIRONMENT or "dev",
        )

        final_status = "completed" if apply_res.status in ("applied", "noop") else "failed"

        new_request = RequestModel(
            id=request_id,
            type=RequestType.TAG_CHANGE.value,
            title=f"Tag change: {dataset_name} (Local)",
            status=final_status,
            current_state=final_status,
            requester_email=current_user.email,
            created_at=now,
            updated_at=now,
            state_context={
                "dataset_id": payload.dataset_id,
                "dataset_name": dataset_name,
                "requested_by": current_user.full_name or current_user.email,
                "requested_by_email": current_user.email,
                "execution_mode": "local",
                "changes": changes,
                "tags_sql": build_tag_sql(changes),
                "plan": plan.to_dict(),
                "risk": risk_report.to_dict(),
                "lint": {"findings": [f.to_dict() for f in lint_findings]},
                "apply_result": apply_res.to_dict(),
                "statements_applied": apply_res.applied_count,
                "statements_noop": apply_res.noop_count,
                "statements_failed": apply_res.failed_count,
                "error": apply_res.error,
                "submitted_at": now.isoformat(),
            },
        )
        db.add(new_request)
        add_fact(db, request_id, "tag_change_applied", apply_res.to_dict(), actor=current_user.email)
        db.commit()

        if apply_res.status == "failed":
            logger.error(f"[{request_id}] Local tag application failed: {apply_res.error}")

        return TagChangeResponse(
            id=request_id,
            title=new_request.title,
            dataset_id=payload.dataset_id,
            status=final_status,
            execution_mode="local",
            table_count=len(changes),
            applied_count=apply_res.applied_count,
            noop_count=apply_res.noop_count,
            failed_count=apply_res.failed_count,
            created_at=now,
            updated_at=now,
        )

    # -----------------------------------------------------------------------
    # GITOPS MODE (PR)
    # -----------------------------------------------------------------------
    if not settings.GOVERNANCE_TAGS_REPO:
        raise HTTPException(
            status_code=400,
            detail="GOVERNANCE_TAGS_REPO is not configured. Configure it in Admin -> Settings or enable Local Execution Mode.",
        )
    if not settings.GOVERNANCE_TAGS_BASE_BRANCH:
        raise HTTPException(
            status_code=400,
            detail="GOVERNANCE_TAGS_BASE_BRANCH is not configured. Configure it in Admin -> Settings or enable Local Execution Mode.",
        )

    sql = build_tag_sql(changes)
    new_request = RequestModel(
        id=request_id,
        type=RequestType.TAG_CHANGE.value,
        title=f"Tag change: {dataset_name}",
        status="pending",
        current_state="pending",
        requester_email=current_user.email,
        created_at=now,
        updated_at=now,
        state_context={
            "dataset_id": payload.dataset_id,
            "dataset_name": dataset_name,
            "requested_by": current_user.full_name or current_user.email,
            "requested_by_email": current_user.email,
            "execution_mode": "gitops",
            "changes": changes,
            "tags_sql": sql,
            "pr_title": payload.pr_title or f"Tag change: {dataset_name}",
            "submitted_at": now.isoformat(),
        },
    )
    db.add(new_request)
    add_fact(db, request_id, "request_submitted", {}, actor=current_user.email)
    db.commit()

    logger.info(f"[{request_id}] Created GitOps tag-change request for dataset '{dataset_name}'")

    return TagChangeResponse(
        id=request_id,
        title=new_request.title,
        dataset_id=payload.dataset_id,
        status="pending",
        execution_mode="gitops",
        table_count=len(changes),
        created_at=now,
        updated_at=now,
    )


@router.get("/changes", response_model=List[TagChangeResponse])
def list_tag_changes(
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(["platform_admin", "governance_admin"])),
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
        pr_fact = get_latest_fact(db, r.id, "pr_created")
        pr_url = pr_fact.event_data.get("pr_url") if pr_fact and pr_fact.event_data else None
        pr_number = pr_fact.event_data.get("pr_number") if pr_fact and pr_fact.event_data else None

        results.append(
            TagChangeResponse(
                id=r.id,
                title=r.title,
                dataset_id=ctx.get("dataset_id"),
                status=r.status,
                execution_mode=ctx.get("execution_mode", "gitops"),
                pr_url=pr_url,
                pr_number=pr_number,
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
    current_user=Depends(require_any_role(["platform_admin", "governance_admin"])),
):
    """Get full details, checks, plan diffs, and execution outcomes for a tag change."""
    request = db.query(RequestModel).filter(RequestModel.id == change_id).first()
    if not request:
        raise HTTPException(status_code=404, detail=f"Tag change request '{change_id}' not found.")

    ctx = request.state_context or {}
    pr_fact = get_latest_fact(db, request.id, "pr_created")
    pr_url = pr_fact.event_data.get("pr_url") if pr_fact and pr_fact.event_data else None
    pr_number = pr_fact.event_data.get("pr_number") if pr_fact and pr_fact.event_data else None

    apply_result = ctx.get("apply_result") or {}
    outcomes = apply_result.get("outcomes")

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
        outcomes=outcomes,
        error=ctx.get("error"),
        created_at=request.created_at,
        updated_at=request.updated_at,
    )
