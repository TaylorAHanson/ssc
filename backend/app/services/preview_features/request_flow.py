"""Open a preview-feature request: one request covers every selected target.

Two entry points share the same validation:

* the Preview Features tab calls :func:`open_request`, which creates the request
  and links its targets in one go;
* the chat agent starts the workflow with ``execute_workflow``, which creates a
  bare request, and the workflow's first step calls :func:`link_request` to
  resolve the feature and link the targets.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.db.preview_feature import ACCOUNT_TARGET, PreviewFeatureModel, PreviewFeatureTargetModel
from app.services.preview_features.feed import normalize
from app.services.preview_features.status import IN_FLIGHT, observed_effective

logger = logging.getLogger(__name__)

REQUEST_TYPE = "preview_feature_request"
ACTIONS = ("enable", "disable")


class PreviewRequestError(ValueError):
    """The request can't be opened as asked (shown to the user as a 400)."""


@dataclass
class OpenedRequest:
    request_id: str
    targets: List[str]


def _is_account(feature: PreviewFeatureModel) -> bool:
    return feature.scope == "account"


def eligible(feature: PreviewFeatureModel, row: Optional[PreviewFeatureTargetModel], action: str) -> Optional[str]:
    """Why ``row`` can't be part of an ``action`` request, or None if it can."""
    if row is None:
        return "not listed in this workspace"
    if row.status in IN_FLIGHT:
        return "a request is already in progress"
    if row.target != ACCOUNT_TARGET and not row.available:
        return "not listed in this workspace"
    eff = observed_effective(row)
    if action == "enable":
        if row.status == "implemented" and not row.drift:
            return "already implemented"
        if eff is True and row.status != "implemented":
            return "already on"
    else:
        if not (row.status == "implemented" or eff is True):
            return "not on"
    return None


def resolve_feature(db: Session, ref: Optional[str]) -> Optional[PreviewFeatureModel]:
    """A tracked, non-archived feature by id, setting name or (normalized) display name."""
    ref = (ref or "").strip()
    if not ref:
        return None
    q = db.query(PreviewFeatureModel).filter(PreviewFeatureModel.archived_at.is_(None))
    for column in (PreviewFeatureModel.id, PreviewFeatureModel.setting_name, PreviewFeatureModel.feed_key):
        hit = q.filter(column == ref).first()
        if hit:
            return hit
    wanted = normalize(ref)
    matches = [f for f in q.all() if normalize(f.display_name) == wanted]
    return matches[0] if len(matches) == 1 else None


def _workspace_names(targets: List[str]) -> List[str]:
    """Accept workspace names or hosts; return names."""
    from app.core.workspaces import get_target_workspaces

    by_host = {w.host.rstrip("/").lower(): w.name for w in get_target_workspaces()}
    out = []
    for t in targets:
        t = (t or "").strip()
        if t:
            out.append(by_host.get(t.rstrip("/").lower(), t))
    return out


def _prepare(db: Session, feature: PreviewFeatureModel, action: str,
             targets: List[str]) -> Tuple[List[str], Dict[str, PreviewFeatureTargetModel]]:
    """Validate a request and return its targets plus their rows (creating the account row)."""
    if action not in ACTIONS:
        raise PreviewRequestError("action must be 'enable' or 'disable'")
    if feature.archived_at:
        raise PreviewRequestError("This feature is no longer in preview")

    rows = {
        r.target: r for r in db.query(PreviewFeatureTargetModel)
        .filter(PreviewFeatureTargetModel.feature_id == feature.id).all()
    }
    if _is_account(feature):
        targets = [ACCOUNT_TARGET]
        if ACCOUNT_TARGET not in rows:
            rows[ACCOUNT_TARGET] = PreviewFeatureTargetModel(
                id=str(uuid.uuid4()), feature_id=feature.id, target=ACCOUNT_TARGET,
                available=True, status="not_requested", drift=False, verify_failures=0,
            )
            db.add(rows[ACCOUNT_TARGET])
    else:
        targets = sorted({t for t in _workspace_names(targets or []) if t != ACCOUNT_TARGET})
        if not targets:
            raise PreviewRequestError("Select at least one workspace")

    problems = [f"{t}: {why}" for t in targets if (why := eligible(feature, rows.get(t), action))]
    if problems:
        raise PreviewRequestError("Can't request this for: " + "; ".join(problems))
    return targets, rows


def _claim(db: Session, rows: Dict[str, PreviewFeatureTargetModel], targets: List[str],
           request_id: Optional[str]) -> None:
    """Atomically move each target to ``requested`` unless another request got there first.

    The eligibility check reads, then this writes; without a compare-and-set two
    requests opened at the same moment would both pass the check and the second
    would silently take over the first's targets. Raises when any target was taken.
    """
    db.flush()  # a new account row must exist before it can be claimed
    taken = []
    for t in targets:
        row = rows[t]
        claimed = (
            db.query(PreviewFeatureTargetModel)
            .filter(PreviewFeatureTargetModel.id == row.id,
                    PreviewFeatureTargetModel.status.notin_(IN_FLIGHT))
            .update({"status": "requested", "request_id": request_id}, synchronize_session=False)
        )
        if not claimed:
            taken.append(t)
    if taken:
        db.rollback()
        raise PreviewRequestError("Can't request this for: " + "; ".join(
            f"{t}: a request is already in progress" for t in taken))
    for t in targets:
        db.refresh(rows[t])


def _title(feature: PreviewFeatureModel, action: str, targets: List[str]) -> str:
    verb = "Enable" if action == "enable" else "Disable"
    where = "account" if _is_account(feature) else ", ".join(targets)
    return f"{verb} preview: {feature.display_name} ({where})"


def _mark_requested(db: Session, feature: PreviewFeatureModel, rows: Dict[str, PreviewFeatureTargetModel],
                    targets: List[str], request_id: str, action: str, user_email: Optional[str]) -> None:
    from app.state_machines.facts import add_fact

    for t in targets:
        row = rows[t]
        row.status = "requested"
        row.action = action
        row.request_id = request_id
        row.requested_by = user_email
        row.approved_by = None
        row.implemented_by = None
        row.verify_failures = 0
        row.note = None
    add_fact(db, request_id, "preview_feature_requested",
             {"feature": feature.display_name, "action": action, "targets": targets}, actor=user_email,
             commit=False)


def open_request(
    db: Session,
    feature: PreviewFeatureModel,
    *,
    action: str,
    targets: List[str],
    justification: Optional[str],
    user_email: str,
    user_name: Optional[str] = None,
) -> OpenedRequest:
    """The tab's path: validate, create the request, link its targets."""
    from app.models.request import RequestCreate
    from app.services.request_service import RequestService

    targets, rows = _prepare(db, feature, action, targets)
    previous = {t: rows[t].status for t in targets}
    _claim(db, rows, targets, None)
    db.commit()
    try:
        request = RequestService.create_request(db, RequestCreate(
            type=REQUEST_TYPE,
            title=_title(feature, action, targets),
            requester_email=user_email,
            metadata={
                # The workflow's declared inputs (what the chat agent passes too).
                "feature": feature.setting_name or feature.id,
                "action": action,
                "targets": targets,
                "justification": (justification or "").strip(),
                # Extra context for approvers and the tab.
                "feature_id": feature.id,
                "setting_name": feature.setting_name,
                "display_name": feature.display_name,
                "scope": "account" if _is_account(feature) else "workspace",
                "value_type": feature.value_type or "other",
                "requested_by": user_name or user_email,
                "requested_by_email": user_email,
            },
        ))
    except Exception:
        # Release the claim so the targets can be requested again.
        db.rollback()
        for t in targets:
            rows[t].status = previous[t]
        db.commit()
        raise
    _mark_requested(db, feature, rows, targets, request.id, action, user_email)
    db.commit()
    logger.info("Opened %s request %s for '%s' on %s", action, request.id, feature.display_name, targets)
    return OpenedRequest(request_id=request.id, targets=targets)


def link_request(
    db: Session,
    request_id: str,
    *,
    feature_ref: Optional[str],
    action: Optional[str],
    targets: Optional[List[str]],
) -> Tuple[PreviewFeatureModel, List[str]]:
    """The agent's path: link an already-created request to its feature and targets.

    Called by the workflow's first step when the request wasn't opened from the
    tab. Raises :class:`PreviewRequestError` when the feature can't be resolved or
    a target isn't eligible, which fails the request with that message.
    """
    from app.db import RequestModel

    request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    if request is None:
        raise PreviewRequestError(f"Request {request_id} not found")
    feature = resolve_feature(db, feature_ref)
    if feature is None:
        raise PreviewRequestError(
            f"No tracked preview matches '{feature_ref}'. Use the feature's setting name from "
            "find_preview_features."
        )
    action = (action or "enable").strip().lower()
    if isinstance(targets, str):
        targets = [t for t in targets.split(",")]
    targets, rows = _prepare(db, feature, action, list(targets or []))
    _claim(db, rows, targets, request_id)
    _mark_requested(db, feature, rows, targets, request_id, action, request.requester_email)
    ctx = dict(request.state_context or {})
    ctx.update({
        "feature_id": feature.id,
        "setting_name": feature.setting_name,
        "display_name": feature.display_name,
        "scope": "account" if _is_account(feature) else "workspace",
        "value_type": feature.value_type or "other",
        "targets": targets,
        "action": action,
    })
    request.state_context = ctx
    request.title = _title(feature, action, targets)
    db.commit()
    logger.info("Linked agent request %s to '%s' (%s) on %s", request_id, feature.display_name, action, targets)
    return feature, targets
