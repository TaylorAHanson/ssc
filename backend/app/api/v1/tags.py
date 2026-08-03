"""
Governance Tag Management API.

Read current Unity Catalog tags per dataset/table and submit tag changes as a
GitHub pull request (GitOps). The app never runs ``ALTER TABLE ... SET TAGS``
directly; a GitHub Action in the configured tags repo applies the generated SQL
per environment once a governance admin merges the PR.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

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


class TagChangeResponse(BaseModel):
    id: str
    title: str
    dataset_id: Optional[str] = None
    status: str
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    table_count: int = 0
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Databricks helpers
# ---------------------------------------------------------------------------

def _get_provider():
    # Tag management works over the same governed datasets as certification, so
    # it reads UC as the governance SP — the identity holding BROWSE on those
    # catalogs. (Writes still go through GitOps; nothing here alters tags.)
    from app.core.workspaces import get_governance_uc_provider

    return get_governance_uc_provider()


def _discover_dataset_tables(provider, dataset_id: str) -> List[str]:
    """Find the member tables of a dataset (tables sharing the ``dataset`` tag).

    ``dataset_id`` is the ``dataset`` tag value used to group tables into a data
    product (the same identifier the certification tab uses).
    """
    from app.core.workspaces import catalogs_to_scan

    tables: List[str] = []
    # Honour the SCAN_CATALOGS allowlist exactly like contract discovery does —
    # walking every visible catalog here would let this path reach governed data
    # the operator deliberately scoped out.
    catalog_names, _missing = catalogs_to_scan(provider.client)

    safe_id = dataset_id.replace("'", "''")
    for catalog_name in catalog_names:
        query = (
            f"SELECT catalog_name, schema_name, table_name "
            f"FROM {catalog_name}.information_schema.table_tags "
            f"WHERE tag_name = 'dataset' AND tag_value = '{safe_id}'"
        )
        try:
            response = provider.client.statement_execution.execute_statement(
                statement=query,
                warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                wait_timeout="30s",
            )
            if response.result and response.result.data_array:
                for row in response.result.data_array:
                    tables.append(f"{row[0]}.{row[1]}.{row[2]}")
        except Exception as e:
            logger.warning(f"Could not query information_schema for catalog {catalog_name}: {e}")

    return tables


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


def _check_tag_policy(changes: List[dict], resulting_counts: Dict[str, int]) -> List[str]:
    """Run the governance repo's own policy against the change, before submitting.

    Advisory by design: the repo re-runs this on the PR and is the real gate, so
    a policy we can't read must not stop someone filing a request. What it buys
    is the error landing in the UI while the user is still editing, rather than
    as a failed check on a PR they aren't watching.
    """
    from app.workflows.tag_policy import load_policy
    from app.workflows.tools import _get_github_provider

    try:
        policy = asyncio.run(
            load_policy(
                _get_github_provider(),
                repo=settings.GOVERNANCE_TAGS_REPO,
                ref=settings.GOVERNANCE_TAGS_BASE_BRANCH,
            )
        )
    except Exception as e:  # noqa: BLE001 - never block a submit on the advisory check
        logger.warning(f"Tag policy check skipped: {e}")
        return []
    return policy.check(changes, resulting_counts) if policy else []


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/datasets", response_model=List[TagDataset])
def list_tag_datasets(
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(["platform_admin", "governance_admin"])),
):
    """List datasets available for tag management (active data contracts)."""
    contracts = (
        db.query(DataContractModel)
        .filter(DataContractModel.is_active == True)  # noqa: E712
        .all()
    )
    results: List[TagDataset] = []
    for c in contracts:
        parts = (c.dataset_id or "").split(".")
        results.append(
            TagDataset(
                dataset_id=c.dataset_id,
                catalog=parts[0] if len(parts) > 0 and parts[0] else None,
                schema_name=parts[1] if len(parts) > 1 else None,
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
        table_names = _discover_dataset_tables(provider, dataset_id)
        tables = [
            TableTags(table=name, tags=_get_table_tags(provider, name))
            for name in sorted(table_names)
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


@router.post("/changes", response_model=TagChangeResponse)
def create_tag_change(
    payload: TagChangeCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(["platform_admin", "governance_admin"])),
):
    """Diff desired vs current tags, generate SQL, and open a tracked PR."""
    # Check the GitOps target up front: the request is worthless without a repo
    # and branch to open the PR against, and failing here beats stranding it in
    # 'pending' for an admin to untangle.
    if not settings.GOVERNANCE_TAGS_REPO:
        raise HTTPException(
            status_code=400,
            detail="GOVERNANCE_TAGS_REPO is not configured; tag changes cannot be submitted.",
        )
    if not settings.GOVERNANCE_TAGS_BASE_BRANCH:
        raise HTTPException(
            status_code=400,
            detail=(
                "GOVERNANCE_TAGS_BASE_BRANCH is not configured. It must name the governance "
                "repo branch for this environment, since merging into it is what applies "
                "the tags."
            ),
        )

    provider = _get_provider()

    changes: List[dict] = []
    resulting_counts: Dict[str, int] = {}
    for table_input in payload.tables:
        full_name = table_input.table
        desired = {
            k: v for k, v in (table_input.desired_tags or {}).items() if not _is_reserved(k)
        }
        current = _get_table_tags(provider, full_name)

        set_tags = {k: v for k, v in desired.items() if current.get(k) != v}
        unset_tags = [k for k in current if k not in desired]

        if set_tags or unset_tags:
            changes.append({"table": full_name, "set": set_tags, "unset": unset_tags})
            # Undercounts by however many reserved tags the object carries, since
            # those are filtered out of `current`. The repo re-checks against live
            # state, so this only ever misses a violation, never invents one.
            resulting_counts[full_name] = len(desired)

    if not changes:
        raise HTTPException(status_code=400, detail="No tag changes detected.")

    violations = _check_tag_policy(changes, resulting_counts)
    if violations:
        raise HTTPException(
            status_code=400,
            detail="This change would be rejected by the tag policy:\n- "
                   + "\n- ".join(violations),
        )

    sql = build_tag_sql(changes)
    dataset_name = payload.dataset_name or payload.dataset_id

    request_id = f"req-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
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
            "changes": changes,
            "tags_sql": sql,
            "pr_title": payload.pr_title or f"Tag change: {dataset_name}",
            # Names and orders the migration file in the governance repo. Stored
            # rather than taken at PR time so a retried step rewrites the same
            # file instead of adding a second migration.
            "submitted_at": now.isoformat(),
        },
    )
    db.add(new_request)
    # Without this fact the poller never picks the request up and it sits in
    # 'pending' forever — no PR, no error.
    add_fact(db, request_id, "request_submitted", {}, actor=current_user.email)
    db.commit()

    logger.info(
        f"[{request_id}] Created tag-change request for dataset '{dataset_name}' "
        f"({len(changes)} table(s))"
    )

    return TagChangeResponse(
        id=request_id,
        title=new_request.title,
        dataset_id=payload.dataset_id,
        status="pending",
        table_count=len(changes),
        created_at=now,
        updated_at=now,
    )


@router.get("/changes", response_model=List[TagChangeResponse])
def list_tag_changes(
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(["platform_admin", "governance_admin"])),
):
    """List tag-change requests with their PR link and current status."""
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
                pr_url=pr_url,
                pr_number=pr_number,
                table_count=len(ctx.get("changes") or []),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
        )
    return results
