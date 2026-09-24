"""Legacy dashboard → metric view mappings (temporary migration aid).

Users coming from the old BI estate (e.g. Tableau) look up a dashboard they know
and find the governed metric view that replaces it. Admins curate the list in
Admin → Legacy Dashboards; everyone can read it. Domain and subdomain are taken
from the mapped metric view so a mapping follows the view if it's recategorized.
"""
import logging
import uuid
from typing import Any, Dict, Iterable, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api import deps
from app.db.data_asset import DataAssetModel
from app.db.legacy_dashboard import LegacyDashboardMappingModel
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

STATUSES = ("Active", "Migrating", "Deprecated")
Status = Literal["Active", "Migrating", "Deprecated"]
_IMPORT_MAX_ROWS = 1000


def _canonical_status(value: Any) -> Any:
    """Accept any casing ("migrating") and blank (→ Active)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "Active"
    if isinstance(value, str):
        for s in STATUSES:
            if s.lower() == value.strip().lower():
                return s
    return value


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


class LegacyDashboardIn(BaseModel):
    dashboard: str = Field(..., min_length=1, max_length=300)
    metric_view: str = Field(..., min_length=1, description="Metric view full name, or its table name if unique")
    status: Status = "Active"
    owner: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    url: Optional[str] = Field(default=None, max_length=2000)

    _status = field_validator("status", mode="before")(_canonical_status)
    _optional = field_validator("owner", "description", "url", mode="before")(_blank_to_none)

    @field_validator("dashboard", "metric_view", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: Optional[str]) -> Optional[str]:
        if value and not value.lower().startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return value


class LegacyDashboardImport(BaseModel):
    rows: List[Dict[str, Any]] = Field(..., max_length=_IMPORT_MAX_ROWS)


def _resolve_metric_view(db: Session, ref: str) -> Optional[DataAssetModel]:
    """A metric view by full name, or by table name when only one view has it."""
    ref = ref.strip().strip("`").replace("`", "")
    q = db.query(DataAssetModel).filter(DataAssetModel.type == "METRIC_VIEW")
    exact = q.filter(DataAssetModel.id == ref.lower()).first() or q.filter(DataAssetModel.id == ref).first()
    if exact:
        return exact
    if "." in ref:
        return None
    by_name = q.filter(func.lower(DataAssetModel.table_name) == ref.lower()).limit(2).all()
    return by_name[0] if len(by_name) == 1 else None


def _serialize(m: LegacyDashboardMappingModel, mv: Optional[DataAssetModel]) -> Dict[str, Any]:
    return {
        "id": m.id,
        "dashboard": m.dashboard,
        "description": m.description,
        "status": m.status,
        "owner": m.owner,
        "url": m.url,
        "metric_view_id": m.metric_view_id,
        "metric_view": mv.table_name if mv else m.metric_view_id.split(".")[-1],
        "metric_view_description": mv.description if mv else None,
        "domain": mv.domain if mv else None,
        "subdomain": mv.subdomain if mv else None,
        # False when the view has left the catalog since the mapping was made.
        "in_catalog": mv is not None,
        "updated_by": m.updated_by,
        "updated_at": m.updated_at,
    }


def _serialize_all(db: Session, mappings: Iterable[LegacyDashboardMappingModel]) -> List[Dict[str, Any]]:
    mappings = list(mappings)
    ids = {m.metric_view_id for m in mappings}
    views = {a.id: a for a in db.query(DataAssetModel).filter(DataAssetModel.id.in_(ids)).all()} if ids else {}
    return [_serialize(m, views.get(m.metric_view_id)) for m in mappings]


def mappings_by_metric_view(db: Session, metric_view_ids: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Mapped legacy dashboards per metric view id, for callers outside this router."""
    ids = set(metric_view_ids)
    if not ids:
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    rows = db.query(LegacyDashboardMappingModel).filter(LegacyDashboardMappingModel.metric_view_id.in_(ids)).all()
    for m in rows:
        out.setdefault(m.metric_view_id, []).append({"dashboard": m.dashboard, "status": m.status, "owner": m.owner})
    return out


def _is_duplicate(db: Session, dashboard: str, metric_view_id: str, exclude_id: Optional[str] = None) -> bool:
    q = db.query(LegacyDashboardMappingModel).filter(
        LegacyDashboardMappingModel.metric_view_id == metric_view_id,
        func.lower(LegacyDashboardMappingModel.dashboard) == dashboard.lower(),
    )
    if exclude_id:
        q = q.filter(LegacyDashboardMappingModel.id != exclude_id)
    return db.query(q.exists()).scalar()


def _apply(db: Session, entry: LegacyDashboardIn, target: LegacyDashboardMappingModel, user: User,
           exclude_id: Optional[str] = None) -> DataAssetModel:
    mv = _resolve_metric_view(db, entry.metric_view)
    if not mv:
        raise HTTPException(status_code=422, detail=f"No metric view named '{entry.metric_view}' in the catalog.")
    if _is_duplicate(db, entry.dashboard, mv.id, exclude_id):
        raise HTTPException(status_code=409, detail=f"'{entry.dashboard}' is already mapped to {mv.table_name}.")
    target.dashboard = entry.dashboard
    target.metric_view_id = mv.id
    target.status = entry.status
    target.owner = entry.owner
    target.description = entry.description
    target.url = entry.url
    target.updated_by = user.email
    return mv


@router.get("")
@router.get("/")
def list_legacy_dashboards(
    domain: Optional[str] = None,
    subdomain: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> List[Dict[str, Any]]:
    """All mappings, optionally scoped to the metric view's domain / subdomain."""
    q = db.query(LegacyDashboardMappingModel)
    if status:
        q = q.filter(LegacyDashboardMappingModel.status == _canonical_status(status))
    rows = _serialize_all(db, q.order_by(LegacyDashboardMappingModel.dashboard).all())
    if domain:
        rows = [r for r in rows if r["domain"] == domain]
    if subdomain:
        rows = [r for r in rows if r["subdomain"] == subdomain]
    return rows


@router.post("")
@router.post("/")
def create_legacy_dashboard(
    entry: LegacyDashboardIn,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_role("Platform Admin")),
) -> Dict[str, Any]:
    m = LegacyDashboardMappingModel(id=str(uuid.uuid4()))
    mv = _apply(db, entry, m, current_user)
    db.add(m)
    db.commit()
    db.refresh(m)
    logger.info("Legacy dashboard mapping created by %s: %s -> %s", current_user.email, m.dashboard, m.metric_view_id)
    return _serialize(m, mv)


@router.put("/{mapping_id}")
def update_legacy_dashboard(
    mapping_id: str,
    entry: LegacyDashboardIn,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_role("Platform Admin")),
) -> Dict[str, Any]:
    m = db.query(LegacyDashboardMappingModel).filter(LegacyDashboardMappingModel.id == mapping_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Mapping not found")
    mv = _apply(db, entry, m, current_user, exclude_id=mapping_id)
    db.commit()
    db.refresh(m)
    logger.info("Legacy dashboard mapping %s updated by %s", mapping_id, current_user.email)
    return _serialize(m, mv)


@router.delete("/{mapping_id}")
def delete_legacy_dashboard(
    mapping_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_role("Platform Admin")),
) -> Dict[str, Any]:
    m = db.query(LegacyDashboardMappingModel).filter(LegacyDashboardMappingModel.id == mapping_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Mapping not found")
    db.delete(m)
    db.commit()
    logger.info("Legacy dashboard mapping %s (%s) deleted by %s", mapping_id, m.dashboard, current_user.email)
    return {"deleted": mapping_id}


@router.post("/import")
def import_legacy_dashboards(
    payload: LegacyDashboardImport,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_role("Platform Admin")),
) -> Dict[str, Any]:
    """Add many mappings at once (pasted CSV). Bad rows are reported, not fatal; repeats are skipped."""
    created, skipped, errors = 0, 0, []
    seen: set = set()
    for index, raw in enumerate(payload.rows, start=1):
        try:
            entry = LegacyDashboardIn.model_validate(raw)
        except ValidationError as e:
            first = e.errors()[0]
            field = ".".join(str(p) for p in first["loc"]) or "row"
            errors.append({"row": index, "error": f"{field}: {first['msg']}"})
            continue
        mv = _resolve_metric_view(db, entry.metric_view)
        if not mv:
            errors.append({"row": index, "error": f"No metric view named '{entry.metric_view}' in the catalog."})
            continue
        key = (entry.dashboard.lower(), mv.id)
        if key in seen or _is_duplicate(db, entry.dashboard, mv.id):
            skipped += 1
            continue
        seen.add(key)
        m = LegacyDashboardMappingModel(id=str(uuid.uuid4()))
        m.dashboard, m.metric_view_id, m.status = entry.dashboard, mv.id, entry.status
        m.owner, m.description, m.url = entry.owner, entry.description, entry.url
        m.updated_by = current_user.email
        db.add(m)
        created += 1
    db.commit()
    logger.info("Legacy dashboard import by %s: %d created, %d skipped, %d errors",
                current_user.email, created, skipped, len(errors))
    return {"created": created, "skipped": skipped, "errors": errors}
