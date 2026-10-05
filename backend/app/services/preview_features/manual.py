"""Previews added by hand, before Databricks lists them anywhere.

Some previews are switched on for an account by Databricks before any public
announcement, so neither the settings API nor the docs feed knows about them.
An admin adds one here with free text; it gets target rows like any other
feature and goes through the same request workflow, where a person makes the
change and their completion of the Implement task is the confirmation.

Usually a real setting shows up later. :func:`match` then folds the manual
feature into the synced one: open requests, approvals and implemented statuses
move to the synced feature's targets (so the workflow and the sync check the
real setting from then on), and the manual row is archived as ``matched``.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from app.db.preview_feature import ACCOUNT_TARGET, PreviewFeatureModel, PreviewFeatureTargetModel
from app.services.preview_features.feed import https_url, name_tokens, normalize
from app.services.preview_features.settings_api import PREVIEW_PHASES
from app.services.preview_features.status import (
    IN_FLIGHT, observed_effective, record_verification, wanted_value,
)

logger = logging.getLogger(__name__)

MANUAL = "manual"
SCOPES = ("workspace", "account")
# Statuses that carry over when a manual feature is matched; the rest is history in the request facts.
_CARRIED_STATUSES = ("requested", "approved", "implemented")
_CARRIED_FIELDS = (
    "status", "action", "request_id", "requested_by", "approved_by", "implemented_by",
    "verification", "last_verified_at", "verify_failures", "note",
)
_MIN_SUGGESTION_SCORE = 30.0
# Words people add to a hand-typed name that say nothing about which feature it is.
_FILLER = {"early", "access", "private", "public", "preview", "beta", "feature", "features",
           "enable", "enabled", "mode", "program", "pilot"}


class ManualFeatureError(ValueError):
    """The change can't be made as asked (shown to the user as a 400)."""


def is_manual(feature: PreviewFeatureModel) -> bool:
    return feature.origin == MANUAL


def _clean_name(name: Optional[str]) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ManualFeatureError("Give the feature a name")
    if len(name) > 200:
        raise ManualFeatureError("Keep the name under 200 characters")
    return name


def _check_phase(phase: Optional[str]) -> str:
    phase = (phase or "PRIVATE_PREVIEW").strip().upper()
    if phase not in PREVIEW_PHASES:
        raise ManualFeatureError("Phase must be Beta, Public Preview or Private Preview")
    return phase


def _check_unique(db: Session, name: str, exclude_id: Optional[str] = None) -> None:
    wanted = normalize(name)
    for f in db.query(PreviewFeatureModel).filter(PreviewFeatureModel.archived_at.is_(None)).all():
        if f.id != exclude_id and normalize(f.display_name) == wanted:
            raise ManualFeatureError(f"'{f.display_name}' is already on the list; request that one instead")


def ensure_workspace_targets(db: Session, feature: PreviewFeatureModel, workspaces: List[str]) -> int:
    """Give a manual workspace feature a requestable row in each workspace. Returns rows added.

    Nothing lists a manual feature, so every target workspace counts as available.
    """
    rows = {
        r.target: r for r in db.query(PreviewFeatureTargetModel)
        .filter(PreviewFeatureTargetModel.feature_id == feature.id).all()
    }
    added = 0
    for name in workspaces:
        row = rows.get(name)
        if row is None:
            db.add(PreviewFeatureTargetModel(
                id=str(uuid.uuid4()), feature_id=feature.id, target=name,
                available=True, status="not_requested", drift=False, verify_failures=0,
            ))
            added += 1
        elif not row.available:
            row.available = True
    return added


def _ensure_account_target(db: Session, feature: PreviewFeatureModel) -> None:
    exists = db.query(PreviewFeatureTargetModel).filter(
        PreviewFeatureTargetModel.feature_id == feature.id,
        PreviewFeatureTargetModel.target == ACCOUNT_TARGET,
    ).first()
    if not exists:
        db.add(PreviewFeatureTargetModel(
            id=str(uuid.uuid4()), feature_id=feature.id, target=ACCOUNT_TARGET,
            available=True, status="not_requested", drift=False, verify_failures=0,
        ))


def _target_workspace_names() -> List[str]:
    from app.core.workspaces import get_target_workspaces

    return [w.name for w in get_target_workspaces()]


def create(
    db: Session,
    *,
    display_name: str,
    description: Optional[str],
    scope: str,
    phase: Optional[str],
    docs_link: Optional[str],
    user_email: Optional[str],
) -> PreviewFeatureModel:
    """Add a feature by hand. The caller commits."""
    name = _clean_name(display_name)
    if scope not in SCOPES:
        raise ManualFeatureError("Scope must be 'workspace' or 'account'")
    link = (docs_link or "").strip()
    if link and not https_url(link):
        raise ManualFeatureError("The docs link must be an https URL")
    _check_unique(db, name)
    now = datetime.utcnow()
    feature = PreviewFeatureModel(
        id=str(uuid.uuid4()), display_name=name,
        description=(description or "").strip()[:4000] or None,
        phase=_check_phase(phase), scope=scope, scope_source="admin", value_type="other",
        docs_link=link or None, docs_link_source="admin" if link else None,
        origin=MANUAL, created_by=user_email, first_seen_at=now, last_seen_at=now, missing_syncs=0,
    )
    db.add(feature)
    db.flush()
    if scope == "account":
        _ensure_account_target(db, feature)
    else:
        ensure_workspace_targets(db, feature, _target_workspace_names())
    logger.info("Preview '%s' (%s) added by hand by %s", name, scope, user_email)
    return feature


def _has_inflight(db: Session, feature: PreviewFeatureModel) -> bool:
    return db.query(PreviewFeatureTargetModel).filter(
        PreviewFeatureTargetModel.feature_id == feature.id,
        PreviewFeatureTargetModel.status.in_(IN_FLIGHT),
    ).first() is not None


def update(
    db: Session,
    feature: PreviewFeatureModel,
    *,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    phase: Optional[str] = None,
) -> None:
    """Edit a manual feature's text. Synced features take these from Databricks. The caller commits."""
    if not is_manual(feature):
        raise ManualFeatureError("Only features added by hand can be renamed or described here")
    if display_name is not None:
        name = _clean_name(display_name)
        _check_unique(db, name, exclude_id=feature.id)
        feature.display_name = name
    if description is not None:
        feature.description = description.strip()[:4000] or None
    if phase is not None:
        new_phase = _check_phase(phase)
        if feature.phase != new_phase:
            feature.phase = new_phase
            feature.phase_changed_at = datetime.utcnow()


def set_scope(db: Session, feature: PreviewFeatureModel, scope: str) -> None:
    """Switch a manual feature between workspace and account level. The caller commits."""
    if scope == "account":
        _ensure_account_target(db, feature)
    else:
        ensure_workspace_targets(db, feature, _target_workspace_names())


def remove(db: Session, feature: PreviewFeatureModel, *, user_email: Optional[str]) -> None:
    """Take a manual feature off the list (archived, so its requests keep their history)."""
    if not is_manual(feature):
        raise ManualFeatureError("Only features added by hand can be removed")
    if feature.archived_at:
        raise ManualFeatureError("This feature was already matched or removed")
    if _has_inflight(db, feature):
        raise ManualFeatureError("Finish or reject the open request before removing this feature")
    feature.archived_at = datetime.utcnow()
    feature.archived_reason = "removed"
    logger.info("Manual preview '%s' removed by %s", feature.display_name, user_email)


# ---------------------------------------------------------------------------
# Matching a manual feature to the synced one
# ---------------------------------------------------------------------------

def _words(*texts: Optional[str]) -> Set[str]:
    from app.services.preview_features.search import _COMMON

    words = set()
    for t in texts:
        words |= set(name_tokens((t or "").replace("_", " ")))
    return (words - _COMMON - _FILLER) or words


def suggestions(manual: PreviewFeatureModel, candidates: List[PreviewFeatureModel],
                limit: int = 3) -> List[Dict[str, Any]]:
    """Synced features whose name resembles the manual one, best first.

    A hand-typed name often carries extra words ("... early access"), so this
    scores word overlap both ways rather than how much of one name the other
    contains. A feature first seen after the manual one was added gets a boost:
    that's when its setting would appear.
    """
    from app.services.preview_features.search import _score

    mine = _words(manual.display_name)
    scored = []
    for f in candidates:
        if f.id == manual.id or is_manual(f) or f.archived_at:
            continue
        theirs = _words(f.display_name, f.setting_name)
        overlap = 200.0 * len(mine & theirs) / (len(mine) + len(theirs)) if mine and theirs else 0.0
        score = max(overlap, _score(manual.display_name, f))
        if score < _MIN_SUGGESTION_SCORE:
            continue
        newer = bool(manual.first_seen_at and f.first_seen_at and f.first_seen_at >= manual.first_seen_at)
        scored.append((score + (15.0 if newer else 0.0), newer, f))
    scored.sort(key=lambda x: -x[0])
    return [
        {"id": f.id, "display_name": f.display_name, "setting_name": f.setting_name,
         "scope": f.scope, "new_since_added": newer}
        for _, newer, f in scored[:limit]
    ]


@dataclass
class MatchResult:
    feature: PreviewFeatureModel
    moved_targets: List[str]
    request_ids: List[str]


def _destination(target: str, real: PreviewFeatureModel) -> Optional[str]:
    """Where a manual row's state goes on the synced feature, or None if the scopes don't line up."""
    real_is_account = real.scope == "account"
    if target == ACCOUNT_TARGET:
        return ACCOUNT_TARGET if real_is_account else None
    return None if real_is_account else target


def _relink_requests(db: Session, manual: PreviewFeatureModel, real: PreviewFeatureModel,
                     rows: List[PreviewFeatureTargetModel], user_email: Optional[str]) -> List[str]:
    from app.db import RequestModel
    from app.services.preview_features.request_flow import _title
    from app.state_machines.facts import add_fact

    by_request: Dict[str, List[PreviewFeatureTargetModel]] = {}
    for r in rows:
        if r.request_id:
            by_request.setdefault(r.request_id, []).append(r)
    for rid, req_rows in by_request.items():
        request = db.query(RequestModel).filter(RequestModel.id == rid).first()
        if request is None:
            continue
        ctx = dict(request.state_context or {})
        ctx.update({
            "feature": real.setting_name or real.id,
            "feature_id": real.id,
            "setting_name": real.setting_name,
            "display_name": real.display_name,
            "value_type": real.value_type or "other",
        })
        request.state_context = ctx
        if request.status not in ("completed", "rejected", "failed"):
            targets = sorted(r.target for r in req_rows if r.target != ACCOUNT_TARGET)
            request.title = _title(real, req_rows[0].action or "enable", targets)
        add_fact(db, rid, "preview_feature_matched", {
            "from": manual.display_name, "to": real.display_name, "setting_name": real.setting_name,
            "targets": sorted(r.target for r in req_rows),
        }, actor=user_email, commit=False)
    return sorted(by_request)


def match(db: Session, manual: PreviewFeatureModel, real: PreviewFeatureModel, *,
          user_email: Optional[str]) -> MatchResult:
    """Replace ``manual`` with the synced feature ``real``. The caller commits.

    Each manual target with a request moves onto the same target of ``real``,
    then is re-checked against the real setting: an approved target whose value
    is already right becomes implemented, and an implemented one that's off is
    flagged as drift. Refuses when the scopes disagree for a target that has a
    request, or when ``real`` already has its own open request on that target.
    """
    if not is_manual(manual):
        raise ManualFeatureError("Only features added by hand can be matched")
    if manual.archived_at:
        raise ManualFeatureError("This feature was already matched or removed")
    if real.id == manual.id or is_manual(real):
        raise ManualFeatureError("Pick a feature found by the sync")
    if real.archived_at:
        raise ManualFeatureError(f"'{real.display_name}' is no longer in preview")

    manual_rows = db.query(PreviewFeatureTargetModel).filter(
        PreviewFeatureTargetModel.feature_id == manual.id).all()
    real_rows = {
        r.target: r for r in db.query(PreviewFeatureTargetModel)
        .filter(PreviewFeatureTargetModel.feature_id == real.id).all()
    }
    carry = [r for r in manual_rows if r.status in _CARRIED_STATUSES]

    if any(_destination(r.target, real) is None for r in carry):
        raise ManualFeatureError(
            f"'{manual.display_name}' is {manual.scope}-level but '{real.display_name}' is "
            f"{'account' if real.scope == 'account' else 'workspace'}-level. Change the scope of one "
            "so they agree, then match again."
        )
    busy = sorted(
        "Account" if r.target == ACCOUNT_TARGET else r.target
        for r in carry
        if (dest := real_rows.get(_destination(r.target, real) or "")) is not None and dest.status in IN_FLIGHT
    )
    if busy:
        raise ManualFeatureError(
            f"'{real.display_name}' already has its own open request for {', '.join(busy)}. "
            "Finish or reject it first."
        )

    now = datetime.utcnow()
    moved: List[PreviewFeatureTargetModel] = []
    for r in carry:
        target = _destination(r.target, real)
        dest = real_rows.get(target)
        if dest is None:
            # The synced feature isn't listed there (yet): move the row itself.
            r.feature_id = real.id
            r.available = target == ACCOUNT_TARGET
            r.observed_value = r.observed_at = r.observe_error = None
            dest = r
        else:
            for name in _CARRIED_FIELDS:
                setattr(dest, name, getattr(r, name))
            db.delete(r)
        moved.append(dest)
    for r in manual_rows:
        if r not in carry:
            db.delete(r)
    db.flush()

    for row in moved:
        if row.status == "approved":
            record_verification(db, real, row, actor=row.implemented_by, now=now)
        elif row.status == "implemented":
            eff = observed_effective(row)
            row.drift = eff is not None and eff != wanted_value(row)

    if not real.docs_link and manual.docs_link:
        real.docs_link, real.docs_link_source = manual.docs_link, "admin"
    if not real.probe and manual.probe:
        real.probe = manual.probe

    request_ids = _relink_requests(db, manual, real, moved, user_email)
    manual.archived_at = now
    manual.archived_reason = "matched"
    manual.replaced_by = real.id
    logger.info("Manual preview '%s' matched to '%s' by %s (targets %s, requests %s)",
                manual.display_name, real.display_name, user_email, [r.target for r in moved], request_ids)
    return MatchResult(feature=real, moved_targets=sorted(r.target for r in moved), request_ids=request_ids)
