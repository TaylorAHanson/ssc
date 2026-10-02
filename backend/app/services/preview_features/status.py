"""Per-target status transitions shared by the sync, the workflow tools and the API.

``not_requested -> requested -> approved -> implemented`` (or ``rejected``).
A disable request uses the same path with ``action == "disable"``, and a
verified disable goes back to ``not_requested`` (the request keeps the record).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from sqlalchemy.orm import Session

from app.db.preview_feature import ACCOUNT_TARGET, PreviewFeatureModel, PreviewFeatureTargetModel

logger = logging.getLogger(__name__)

STATUSES = ("not_requested", "requested", "approved", "implemented", "rejected")
IN_FLIGHT = ("requested", "approved")
_TERMINAL_REQUEST = ("completed", "rejected", "failed")


def wanted_value(row: PreviewFeatureTargetModel) -> bool:
    return row.action != "disable"


def observed_effective(row: PreviewFeatureTargetModel) -> Optional[bool]:
    eff = (row.observed_value or {}).get("effective")
    return eff if isinstance(eff, bool) else None


def mark_passed(row: PreviewFeatureTargetModel, method: str, actor: Optional[str], now: datetime) -> None:
    row.last_verified_at = now
    row.verify_failures = 0
    row.drift = False
    if row.action == "disable":
        row.status = "not_requested"
        row.verification = None
        row.note = f"Turned off ({method}) on {now:%Y-%m-%d}"
    else:
        row.status = "implemented"
        row.verification = method
        row.note = None
    if actor and not row.implemented_by:
        row.implemented_by = actor


def record_verification(
    db: Session,
    feature: PreviewFeatureModel,
    row: PreviewFeatureTargetModel,
    *,
    actor: Optional[str],
    now: Optional[datetime] = None,
) -> bool:
    """Check an approved target's observed value against what its request wants.

    Uses the value already on the row (the caller refreshes it). Feed-only
    account previews have no value to read; they pass only by attestation in the
    verify step. Returns True when the target passed.
    """
    now = now or datetime.utcnow()
    eff = observed_effective(row)
    if eff is None:
        return False
    if eff == wanted_value(row):
        mark_passed(row, "api", actor, now)
        return True
    row.verify_failures = (row.verify_failures or 0) + 1
    row.note = "Waiting for the change to show up; the daily sync checks again."
    return False


def rows_for_request(db: Session, request_id: str,
                     targets: Optional[Iterable[str]] = None) -> List[PreviewFeatureTargetModel]:
    q = db.query(PreviewFeatureTargetModel).filter(PreviewFeatureTargetModel.request_id == request_id)
    rows = q.all()
    if targets:
        wanted = set(targets)
        rows = [r for r in rows if r.target in wanted]
    return rows


def close_inflight_requests(db: Session, feature: PreviewFeatureModel, reason: str) -> List[str]:
    """Close open requests for an archived feature so they don't wait forever."""
    from app.db import ApprovalModel, RequestModel
    from app.state_machines.facts import add_fact

    rows = (
        db.query(PreviewFeatureTargetModel)
        .filter(PreviewFeatureTargetModel.feature_id == feature.id,
                PreviewFeatureTargetModel.status.in_(IN_FLIGHT))
        .all()
    )
    request_ids = sorted({r.request_id for r in rows if r.request_id})
    now = datetime.now(timezone.utc)
    for rid in request_ids:
        request = db.query(RequestModel).filter(RequestModel.id == rid).first()
        if request is None or request.status in _TERMINAL_REQUEST:
            continue
        request.status = "completed"
        request.current_state = "completed"
        request.updated_at = now
        (db.query(ApprovalModel)
         .filter(ApprovalModel.request_id == rid, ApprovalModel.status == "pending")
         .update({"status": "cancelled", "rejection_note": f"Closed: {reason}"},
                 synchronize_session=False))
        add_fact(db, rid, "preview_feature_closed", {"reason": reason, "feature": feature.display_name},
                 actor="system", commit=False)
        logger.info("Closed preview request %s for '%s': %s", rid, feature.display_name, reason)
    for r in rows:
        r.status = "not_requested"
        r.note = f"Request closed: {reason}"
    return request_ids


def target_label(target: str) -> str:
    return "Account" if target == ACCOUNT_TARGET else target
