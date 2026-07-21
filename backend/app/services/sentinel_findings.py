"""Read/write helpers for a sentinel run's per-record findings.

The full detail of a sentinel run (every violation and every check) lives in the
``sentinel_findings`` table — one row per record — instead of inline in
``requests.state_context``. See :mod:`app.db.sentinel_finding` for why.

This module is the single place that knows how a finding dict maps to a row, so
the write path (discovery) and every read path (enforcement, notify, the detail
API) stay in lockstep.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from app.db.sentinel_finding import SentinelFindingModel

logger = logging.getLogger(__name__)

#: Insert findings in batches so a huge run is never one giant statement (which
#: is exactly what dropped the DB connection when we wrote them inline).
_INSERT_BATCH = 1000


def _workspace_name(rec: Dict[str, Any]) -> Optional[str]:
    """Resolve a finding's workspace name (dict, string, or OPA-input fallback)."""
    ws = rec.get("workspace")
    if isinstance(ws, dict):
        name = ws.get("name")
        if name:
            return str(name)
    elif isinstance(ws, str) and ws:
        return ws
    ic_ws = (rec.get("input_context") or {}).get("workspace") or {}
    if isinstance(ic_ws, dict) and ic_ws.get("name"):
        return str(ic_ws["name"])
    return None


def _owner(rec: Dict[str, Any]) -> Optional[str]:
    owner = rec.get("owner")
    if owner:
        return str(owner)
    ic_owner = ((rec.get("input_context") or {}).get("resource") or {}).get("owner")
    return str(ic_owner) if ic_owner else None


def _search_text(rec: Dict[str, Any], workspace: Optional[str], owner: Optional[str]) -> str:
    parts: List[str] = [
        str(rec.get("resource_id") or ""),
        str(rec.get("resource_type") or ""),
        str(rec.get("policy") or ""),
        str(rec.get("action") or ""),
        workspace or "",
        owner or "",
        str(rec.get("reason") or ""),
    ]
    reasons = rec.get("violation_reasons")
    if isinstance(reasons, list):
        parts.extend(str(r) for r in reasons)
    return " ".join(p for p in parts if p).lower()


def _to_row(request_id: str, kind: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    workspace = _workspace_name(rec)
    owner = _owner(rec)
    return {
        "id": str(uuid.uuid4()),
        "request_id": request_id,
        "kind": kind,
        "workspace": workspace,
        "resource_id": (str(rec.get("resource_id")) if rec.get("resource_id") is not None else None),
        "resource_type": (str(rec.get("resource_type")) if rec.get("resource_type") is not None else None),
        "policy": (str(rec.get("policy")) if rec.get("policy") is not None else None),
        "severity": (str(rec.get("severity")).upper() if rec.get("severity") else None),
        "action": (str(rec.get("action")) if rec.get("action") is not None else None),
        "owner": owner,
        "search_text": _search_text(rec, workspace, owner),
        "data": rec,
    }


def replace_run_findings(
    db: Session,
    request_id: str,
    *,
    violations: List[Dict[str, Any]],
    checks: List[Dict[str, Any]],
) -> None:
    """Persist a run's full findings, replacing any prior rows for the run.

    Deletes existing findings for ``request_id`` first (idempotent re-runs), then
    bulk-inserts violations and checks in batches. Committed here so downstream
    poller steps (enforcement, notify) read a consistent set from the table.
    """
    try:
        db.query(SentinelFindingModel).filter(
            SentinelFindingModel.request_id == request_id
        ).delete(synchronize_session=False)
        db.flush()

        # Stream row-mappings into the DB one batch at a time instead of
        # materializing the full ``rows`` list. A large run can hold hundreds of
        # thousands of checks; building a second complete copy here (each with an
        # extra ``search_text`` string) on top of the caller's in-memory
        # violations+checks was doubling peak memory right at the end of the scan
        # and OOM-killing the app. Peak extra is now bounded to one batch.
        batch: List[Dict[str, Any]] = []
        total = 0
        v_count = 0
        c_count = 0

        def _flush_batch() -> None:
            nonlocal total
            if not batch:
                return
            db.bulk_insert_mappings(SentinelFindingModel, batch)
            db.flush()
            total += len(batch)
            batch.clear()

        for kind, recs in (("violation", violations), ("check", checks)):
            for rec in (recs or []):
                if not isinstance(rec, dict):
                    continue
                batch.append(_to_row(request_id, kind, rec))
                if kind == "violation":
                    v_count += 1
                else:
                    c_count += 1
                if len(batch) >= _INSERT_BATCH:
                    _flush_batch()
        _flush_batch()

        db.commit()
        logger.info(
            "Sentinel: persisted %d finding rows (%d violations, %d checks) for run %s",
            total, v_count, c_count, request_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Sentinel: failed to persist findings for %s: %s", request_id, e)
        db.rollback()
        raise


def load_run_violations(db: Session, request: Any) -> List[Dict[str, Any]]:
    """All violation records for a run, in insertion order (full detail).

    Reads the joined findings table, falling back to the inline
    ``state_context.violations`` for runs created before findings were moved to
    the table (so old runs' digest/enforce keep working).
    """
    return _load_with_fallback(db, request, "violation", "violations")


def load_run_checks(db: Session, request: Any) -> List[Dict[str, Any]]:
    """All check records for a run, in insertion order (full detail).

    Falls back to inline ``state_context.checks`` for pre-migration runs.
    """
    return _load_with_fallback(db, request, "check", "checks")


def _load_with_fallback(
    db: Session, request: Any, kind: str, ctx_key: str
) -> List[Dict[str, Any]]:
    rows = _load(db, request.id, kind)
    if rows:
        return rows
    return (getattr(request, "state_context", None) or {}).get(ctx_key, []) or []


def _load(db: Session, request_id: str, kind: str) -> List[Dict[str, Any]]:
    rows = (
        db.query(SentinelFindingModel.data)
        .filter(
            SentinelFindingModel.request_id == request_id,
            SentinelFindingModel.kind == kind,
        )
        .order_by(SentinelFindingModel.created_at.asc())
        .all()
    )
    return [r[0] for r in rows if r[0] is not None]


def has_findings(db: Session, request_id: str) -> bool:
    """Whether any findings rows exist for a run (i.e. it used the table)."""
    return (
        db.query(SentinelFindingModel.id)
        .filter(SentinelFindingModel.request_id == request_id)
        .first()
        is not None
    )


def query_run_findings(
    db: Session,
    request_id: str,
    *,
    kind: str = "violation",
    search: Optional[str] = None,
    severity: Optional[str] = None,
    policy: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Tuple[List[Dict[str, Any]], int]:
    """A filtered, paginated page of a run's findings plus the matching total.

    Server-side search/filter over the joined table so the detail view can scroll
    the complete set without ever loading it all. Returns ``(items, total)``.
    """
    q = db.query(SentinelFindingModel).filter(
        SentinelFindingModel.request_id == request_id,
        SentinelFindingModel.kind == kind,
    )
    if severity and severity.lower() != "all":
        q = q.filter(SentinelFindingModel.severity == severity.upper())
    if policy and policy.lower() != "all":
        q = q.filter(SentinelFindingModel.policy == policy)
    if search:
        term = f"%{search.strip().lower()}%"
        q = q.filter(
            or_(
                SentinelFindingModel.search_text.ilike(term),
                cast(SentinelFindingModel.data, String).ilike(term),
            )
        )
    total = q.count()
    rows = (
        q.order_by(SentinelFindingModel.created_at.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [r.data for r in rows if r.data is not None], total
