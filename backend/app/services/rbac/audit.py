"""Change sets, rule events, snapshots and the ledger watermark.

Every ledger write (import, approved request, direct edit, revert) opens one
change set and records one event per touched rule, inside the caller's
transaction. Callers commit; nothing here commits except ``committed_watermark``.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db.rbac import RbacChangeSetModel, RbacRuleEventModel, RbacRuleModel
from app.models.rbac import CANONICAL_FIELDS, RuleRow

# Postgres hands out event ids at insert, not at commit, so a later id can commit
# first. Ledger writes hold this lock shared until they commit; the export takes it
# exclusively to read a watermark with no uncommitted ids below it.
WATERMARK_LOCK_KEY = 5_005_202_610_160_002


def _is_postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def new_id() -> str:
    return uuid.uuid4().hex


def compute_row_hash(sheet: str, cells: Mapping[str, Any], extra: Optional[Mapping[str, Any]] = None) -> str:
    """Stable hash of a row's content (sheet + canonical cells + extra), for duplicate detection."""
    payload = {
        "sheet": sheet,
        "cells": {f: str(cells.get(f) or "") for f in CANONICAL_FIELDS},
        "extra": {str(k): str(v or "") for k, v in sorted((extra or {}).items())},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def snapshot(rule: RbacRuleModel) -> Dict[str, Any]:
    """A rule's state as stored in events and returned in diffs."""
    data: Dict[str, Any] = {f: getattr(rule, f) or "" for f in CANONICAL_FIELDS}
    data.update({
        "sheet": rule.sheet,
        "plane": rule.plane,
        "environment": rule.environment,
        "position": rule.position,
        "extra": dict(rule.extra or {}),
        "status": rule.status,
        "version": rule.version,
    })
    return data


def to_row(rule: RbacRuleModel) -> RuleRow:
    return RuleRow(
        id=rule.id,
        sheet=rule.sheet,
        plane=rule.plane,
        environment=rule.environment,
        position=rule.position,
        extra=dict(rule.extra or {}),
        status=rule.status,
        version=rule.version,
        **{f: getattr(rule, f) or "" for f in CANONICAL_FIELDS},
    )


def new_change_set(
    db: Session,
    *,
    source: str,
    source_ref: Optional[str],
    actor: Optional[str],
    reason: Optional[str],
    change_set_id: Optional[str] = None,
) -> RbacChangeSetModel:
    if _is_postgres(db):
        db.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": WATERMARK_LOCK_KEY})
    cs = RbacChangeSetModel(
        id=change_set_id or new_id(),
        source=source,
        source_ref=source_ref,
        actor=actor,
        reason=reason,
        summary={},
        created_at=datetime.utcnow(),
    )
    db.add(cs)
    db.flush()
    return cs


def record_event(
    db: Session,
    change_set: RbacChangeSetModel,
    *,
    rule_id: str,
    action: str,
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
) -> RbacRuleEventModel:
    event = RbacRuleEventModel(
        rule_id=rule_id,
        change_set_id=change_set.id,
        action=action,
        before=before,
        after=after,
        actor=change_set.actor,
        reason=change_set.reason,
        source=change_set.source,
        source_ref=change_set.source_ref,
        created_at=datetime.utcnow(),
    )
    db.add(event)
    return event


def finalize_change_set(change_set: RbacChangeSetModel, *, created: int, updated: int, deleted: int,
                        sheets: Optional[list] = None, planes: Optional[list] = None,
                        **extra: Any) -> None:
    change_set.summary = {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "sheets": sorted(set(sheets or [])),
        "planes": sorted(set(planes or [])),
        **extra,
    }


def watermark(db: Session) -> int:
    """The largest event id: changes whenever the ledger does."""
    return int(db.query(func.max(RbacRuleEventModel.id)).scalar() or 0)


def committed_watermark(db: Session) -> int:
    """``watermark`` once every write already holding event ids has committed; commits the session.

    Later writes get larger ids, so an export never passes over a change that commits late.
    """
    if not _is_postgres(db):
        return watermark(db)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": WATERMARK_LOCK_KEY})
    value = watermark(db)
    db.commit()
    return value
