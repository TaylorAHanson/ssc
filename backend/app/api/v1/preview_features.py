"""Preview Features API: the tracked Beta / Public Preview features and their requests.

- ``GET  /preview-features``                  features with per-target status, the
                                             target workspaces, and sync status
- ``POST /preview-features/sync``             start a sync now (runs in the background)
- ``GET  /preview-features/sync``             the last / running sync
- ``POST /preview-features``                  add a feature by hand (not listed by Databricks yet)
- ``POST /preview-features/{id}/requests``    open an enable or disable request
- ``POST /preview-features/{id}/match``       replace a hand-added feature with the synced one
- ``PATCH /preview-features/{id}``            admin overrides: scope, docs link, probe
                                             (and name, description, phase for hand-added ones)
- ``DELETE /preview-features/{id}``           remove a hand-added feature

Gated by the ``preview_features`` feature flag. The logic lives in
``app.services.preview_features``; this module is HTTP.
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_any_role
from app.core.config import settings
from app.core.feature_flags import is_feature_enabled
from app.db import RequestModel
from app.db.preview_feature import PreviewFeatureModel, PreviewFeatureTargetModel
from app.db.session import get_db

router = APIRouter()
logger = logging.getLogger(__name__)

_ADMIN_ROLES = ["platform_admin", "governance_admin"]
_SCOPES = ("workspace", "account")

# Strong references to sync tasks started from the API (asyncio keeps only weak ones).
_sync_tasks: set = set()


def _require_feature() -> None:
    if not is_feature_enabled("preview_features"):
        raise HTTPException(status_code=404, detail="Preview features are not enabled")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PreviewTarget(BaseModel):
    target: str
    available: bool
    status: str
    action: Optional[str] = None
    request_id: Optional[str] = None
    request_status: Optional[str] = None
    # The open request's pending inbox item, e.g. "platform_admin" or "manual_task".
    pending_approval: Optional[str] = None
    # The current user may approve that item (same check as POST /requests/{id}/approve).
    can_approve: bool = False
    requested_by: Optional[str] = None
    approved_by: Optional[str] = None
    implemented_by: Optional[str] = None
    observed_value: Optional[Dict[str, Any]] = None
    observed_at: Optional[datetime] = None
    observe_error: Optional[str] = None
    verification: Optional[str] = None
    last_verified_at: Optional[datetime] = None
    drift: bool = False
    note: Optional[str] = None


class MatchSuggestion(BaseModel):
    id: str
    display_name: str
    setting_name: Optional[str] = None
    scope: str
    # First seen after the hand-added feature was created: when its setting would appear.
    new_since_added: bool = False


class PreviewFeature(BaseModel):
    id: str
    setting_name: Optional[str] = None
    display_name: str
    description: Optional[str] = None
    phase: Optional[str] = None
    phase_changed_at: Optional[datetime] = None
    scope: str
    scope_source: str
    value_type: Optional[str] = None
    announcement_text: Optional[str] = None
    announcement_url: Optional[str] = None
    announced_at: Optional[datetime] = None
    docs_link: Optional[str] = None
    docs_link_source: Optional[str] = None
    probe: Optional[Dict[str, Any]] = None
    first_seen_at: datetime
    last_seen_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    archived_reason: Optional[str] = None
    # "manual" when an admin added it by hand.
    origin: Optional[str] = None
    created_by: Optional[str] = None
    replaced_by: Optional[str] = None
    # Hand-added features: synced features that look like the same thing.
    match_suggestions: List[MatchSuggestion] = []
    # Synced features: names of hand-added features matched into this one.
    matched_from: List[str] = []
    targets: List[PreviewTarget] = []


class TargetWorkspace(BaseModel):
    name: str
    environment: str
    host: str


class SyncStatus(BaseModel):
    cron: str = ""
    next_run: Optional[str] = None
    last_run: Dict[str, Any] = {}
    last_seen_at: Optional[datetime] = None


class PreviewFeaturesResponse(BaseModel):
    features: List[PreviewFeature]
    workspaces: List[TargetWorkspace]
    sync: SyncStatus


class PreviewRequestCreate(BaseModel):
    action: str = Field(default="enable", description="enable | disable")
    targets: List[str] = Field(default_factory=list, description="Workspace names (ignored for account scope)")
    justification: Optional[str] = Field(default=None, max_length=2000)


class PreviewRequestResponse(BaseModel):
    request_id: str
    targets: List[str]


class PreviewFeatureUpdate(BaseModel):
    scope: Optional[str] = None
    # "" clears an admin link and lets the sync find one again.
    docs_link: Optional[str] = None
    # A read-only REST GET path (e.g. /api/2.0/...) whose success means the feature is on. "" clears it.
    probe_path: Optional[str] = None
    # Hand-added features only; synced ones take these from Databricks.
    display_name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    phase: Optional[str] = None


class ManualFeatureCreate(BaseModel):
    display_name: str = Field(..., max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    scope: str = Field(default="workspace", description="workspace | account")
    phase: Optional[str] = Field(default="PRIVATE_PREVIEW", description="BETA | PUBLIC_PREVIEW | PRIVATE_PREVIEW")
    docs_link: Optional[str] = None


class MatchRequest(BaseModel):
    feature_id: str = Field(..., description="The synced feature that replaces the hand-added one")


class MatchResponse(BaseModel):
    feature: PreviewFeature
    moved_targets: List[str]
    request_ids: List[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sync_status(db: Session) -> SyncStatus:
    from sqlalchemy import func

    from app.services.preview_features import sync

    last_seen = db.query(func.max(PreviewFeatureModel.last_seen_at)).scalar()
    return SyncStatus(
        cron=(getattr(settings, "PREVIEW_FEATURE_SYNC_CRON", "") or "").strip(),
        next_run=sync.next_run(),
        last_run=sync.last_run(),
        last_seen_at=last_seen,
    )


def _pending_approvals(db: Session, request_ids, user) -> Dict[str, Dict[str, Any]]:
    """Per request: its pending inbox item and whether ``user`` may act on it."""
    from app.api.v1.requests import _authorize_approval_actor
    from app.db import ApprovalModel

    out: Dict[str, Dict[str, Any]] = {}
    if not request_ids:
        return out
    pending = (
        db.query(ApprovalModel)
        .filter(ApprovalModel.request_id.in_(list(request_ids)), ApprovalModel.status == "pending")
        .order_by(ApprovalModel.created_at.asc())
        .all()
    )
    for approval in pending:
        if approval.request_id in out:
            continue
        try:
            _authorize_approval_actor(approval, user)
            allowed = True
        except HTTPException:
            allowed = False
        out[approval.request_id] = {"type": approval.approval_type, "can_approve": allowed}
    return out


def _serialize(feature: PreviewFeatureModel, rows: List[PreviewFeatureTargetModel],
               request_status: Dict[str, str],
               approvals: Optional[Dict[str, Dict[str, Any]]] = None,
               *,
               suggestions: Optional[List[Dict[str, Any]]] = None,
               matched_from: Optional[List[str]] = None) -> PreviewFeature:
    data = {c.name: getattr(feature, c.name) for c in PreviewFeatureModel.__table__.columns}
    data["scope"] = data.get("scope") or "workspace"
    data["scope_source"] = data.get("scope_source") or "api"
    targets = []
    for r in sorted(rows, key=lambda r: r.target):
        t = {c.name: getattr(r, c.name) for c in PreviewFeatureTargetModel.__table__.columns}
        t["request_status"] = request_status.get(r.request_id or "")
        pending = (approvals or {}).get(r.request_id or "")
        if pending and r.status in ("requested", "approved"):
            t["pending_approval"] = pending["type"]
            t["can_approve"] = pending["can_approve"]
        targets.append(PreviewTarget(**{k: v for k, v in t.items() if k in PreviewTarget.model_fields}))
    return PreviewFeature(
        **{k: v for k, v in data.items() if k in PreviewFeature.model_fields and k != "targets"},
        targets=targets,
        match_suggestions=[MatchSuggestion(**s) for s in suggestions or []],
        matched_from=matched_from or [],
    )


def _load_feature(db: Session, feature_id: str) -> PreviewFeatureModel:
    feature = db.get(PreviewFeatureModel, feature_id)
    if feature is None:
        raise HTTPException(status_code=404, detail="Preview feature not found")
    return feature


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=PreviewFeaturesResponse)
def list_preview_features(
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Every tracked preview (GA and retired ones only with include_archived)."""
    _require_feature()
    from app.core.workspaces import get_target_workspaces

    q = db.query(PreviewFeatureModel)
    if not include_archived:
        q = q.filter(PreviewFeatureModel.archived_at.is_(None))
    features = q.order_by(PreviewFeatureModel.display_name).all()
    ids = [f.id for f in features]
    rows_by_feature: Dict[str, List[PreviewFeatureTargetModel]] = {}
    if ids:
        for r in db.query(PreviewFeatureTargetModel).filter(PreviewFeatureTargetModel.feature_id.in_(ids)).all():
            rows_by_feature.setdefault(r.feature_id, []).append(r)
    request_ids = {r.request_id for rows in rows_by_feature.values() for r in rows if r.request_id}
    request_status: Dict[str, str] = {}
    if request_ids:
        for rid, st in db.query(RequestModel.id, RequestModel.status).filter(RequestModel.id.in_(request_ids)).all():
            request_status[rid] = st

    approvals = _pending_approvals(db, request_ids, current_user)

    from app.services.preview_features import manual

    suggestions = {
        f.id: manual.suggestions(f, features) for f in features if manual.is_manual(f) and not f.archived_at
    }
    matched_from: Dict[str, List[str]] = {}
    for name, real_id in (
        db.query(PreviewFeatureModel.display_name, PreviewFeatureModel.replaced_by)
        .filter(PreviewFeatureModel.replaced_by.isnot(None)).all()
    ):
        matched_from.setdefault(real_id, []).append(name)

    workspaces = [
        TargetWorkspace(name=w.name, environment=w.environment, host=w.host)
        for w in get_target_workspaces()
    ]
    return PreviewFeaturesResponse(
        features=[
            _serialize(f, rows_by_feature.get(f.id, []), request_status, approvals,
                       suggestions=suggestions.get(f.id), matched_from=matched_from.get(f.id))
            for f in features
        ],
        workspaces=workspaces,
        sync=_sync_status(db),
    )


def _rows(db: Session, feature: PreviewFeatureModel) -> List[PreviewFeatureTargetModel]:
    return db.query(PreviewFeatureTargetModel).filter(PreviewFeatureTargetModel.feature_id == feature.id).all()


@router.post("", response_model=PreviewFeature, status_code=201)
def create_manual_feature(
    payload: ManualFeatureCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Add a preview Databricks doesn't list yet. It's requested like any other one."""
    _require_feature()
    from app.services.preview_features import manual

    try:
        feature = manual.create(
            db, display_name=payload.display_name, description=payload.description, scope=payload.scope,
            phase=payload.phase, docs_link=payload.docs_link, user_email=current_user.email,
        )
    except manual.ManualFeatureError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    db.refresh(feature)
    return _serialize(feature, _rows(db, feature), {})


@router.post("/{feature_id}/match", response_model=MatchResponse)
def match_manual_feature(
    feature_id: str,
    payload: MatchRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Replace a hand-added feature with the synced one, carrying its requests and statuses over."""
    _require_feature()
    from app.services.preview_features import manual

    hand_added = _load_feature(db, feature_id)
    real = _load_feature(db, payload.feature_id)
    try:
        result = manual.match(db, hand_added, real, user_email=current_user.email)
    except manual.ManualFeatureError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    db.refresh(real)
    return MatchResponse(
        feature=_serialize(real, _rows(db, real), {}, matched_from=[hand_added.display_name]),
        moved_targets=result.moved_targets,
        request_ids=result.request_ids,
    )


@router.delete("/{feature_id}", status_code=204)
def remove_manual_feature(
    feature_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Take a hand-added feature off the list. Its past requests keep their history."""
    _require_feature()
    from app.services.preview_features import manual

    feature = _load_feature(db, feature_id)
    try:
        manual.remove(db, feature, user_email=current_user.email)
    except manual.ManualFeatureError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()


@router.get("/sync", response_model=SyncStatus)
def get_sync_status(
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    _require_feature()
    return _sync_status(db)


@router.post("/sync", response_model=SyncStatus, status_code=202)
async def start_sync(
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Start a sync now. It runs in the background; poll ``GET /sync`` for the result."""
    _require_feature()
    from app.services.preview_features import sync

    if not sync.last_run().get("running"):
        async def _run():
            try:
                await sync.run_sync()
            except Exception as e:  # noqa: BLE001 - already logged; the status carries the message
                logger.warning("Manual preview sync failed: %s", e)

        task = asyncio.create_task(_run())
        _sync_tasks.add(task)
        task.add_done_callback(_sync_tasks.discard)
        # Let the task mark itself running before we report status.
        await asyncio.sleep(0)
    logger.info("Preview sync started by %s", current_user.email)
    return _sync_status(db)


@router.post("/{feature_id}/requests", response_model=PreviewRequestResponse, status_code=201)
def create_preview_request(
    feature_id: str,
    payload: PreviewRequestCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Open one request (one batch approval) covering every selected workspace."""
    _require_feature()
    from app.services.preview_features.request_flow import PreviewRequestError, open_request

    feature = _load_feature(db, feature_id)
    try:
        opened = open_request(
            db, feature,
            action=payload.action,
            targets=payload.targets,
            justification=payload.justification,
            user_email=current_user.email,
            user_name=getattr(current_user, "full_name", None),
        )
    except PreviewRequestError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        # Unknown request type: the workflow was deleted or unpublished.
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    return PreviewRequestResponse(request_id=opened.request_id, targets=opened.targets)


@router.patch("/{feature_id}", response_model=PreviewFeature)
def update_preview_feature(
    feature_id: str,
    payload: PreviewFeatureUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_any_role(_ADMIN_ROLES)),
):
    """Admin overrides that later syncs keep: scope, docs link and verification probe.

    Hand-added features can also be renamed, described and re-phased.
    """
    _require_feature()
    import re
    import uuid

    from app.db.preview_feature import ACCOUNT_TARGET
    from app.services.preview_features import manual

    feature = _load_feature(db, feature_id)
    hand_added = manual.is_manual(feature)
    if any(v is not None for v in (payload.display_name, payload.description, payload.phase)):
        try:
            manual.update(db, feature, display_name=payload.display_name,
                          description=payload.description, phase=payload.phase)
        except manual.ManualFeatureError as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(e))
    if payload.scope is not None:
        if payload.scope not in _SCOPES:
            raise HTTPException(status_code=400, detail="scope must be 'workspace' or 'account'")
        in_flight = db.query(PreviewFeatureTargetModel).filter(
            PreviewFeatureTargetModel.feature_id == feature.id,
            PreviewFeatureTargetModel.status.in_(("requested", "approved")),
        ).first()
        if in_flight and payload.scope != feature.scope:
            raise HTTPException(status_code=409, detail="Finish or close the open request before changing scope")
        if payload.scope == "workspace" and not feature.setting_name and not hand_added:
            raise HTTPException(status_code=400, detail="Only previews listed by a workspace can be workspace-scoped")
        feature.scope = payload.scope
        feature.scope_source = "admin"
        if hand_added and payload.scope == "workspace":
            manual.set_scope(db, feature, "workspace")
        if payload.scope == "account" and not db.query(PreviewFeatureTargetModel).filter(
            PreviewFeatureTargetModel.feature_id == feature.id,
            PreviewFeatureTargetModel.target == ACCOUNT_TARGET,
        ).first():
            db.add(PreviewFeatureTargetModel(
                id=str(uuid.uuid4()), feature_id=feature.id, target=ACCOUNT_TARGET,
                available=True, status="not_requested", drift=False, verify_failures=0,
            ))
    if payload.docs_link is not None:
        link = payload.docs_link.strip()
        if link and not re.match(r"^https://[^\s]+$", link):
            raise HTTPException(status_code=400, detail="The docs link must be an https URL")
        feature.docs_link = link or None
        feature.docs_link_source = "admin" if link else None
    if payload.probe_path is not None:
        path = payload.probe_path.strip()
        if path and not re.match(r"^/api/[A-Za-z0-9/_\-.?=&]+$", path):
            raise HTTPException(status_code=400, detail="The probe must be a REST path starting with /api/")
        feature.probe = {"type": "rest", "path": path} if path else None
    db.commit()
    db.refresh(feature)
    logger.info("Preview feature %s updated by %s", feature.id, current_user.email)
    suggestions = None
    if hand_added:
        active = db.query(PreviewFeatureModel).filter(PreviewFeatureModel.archived_at.is_(None)).all()
        suggestions = manual.suggestions(feature, active)
    return _serialize(feature, _rows(db, feature), {}, suggestions=suggestions)
