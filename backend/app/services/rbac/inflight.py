"""Open rbac_access_change requests, and which rules their prepared changes touch.

The workflow's ``prepare`` step stores the prepared change in the request's state
context under ``prepared_change`` (an ``AccessProposal`` dict). Rows a pending
change will edit get a marker in the grid, overlap warnings come from here, and
a reset is blocked while any of these requests is open.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

WORKFLOW_ID = "rbac_access_change"
TERMINAL_STATUSES = frozenset({"completed", "rejected", "failed"})
PREPARED_CONTEXT_KEY = "prepared_change"
# Requests prepared before the rename keep their change under this key.
LEGACY_PREPARED_CONTEXT_KEY = "rbac_plan"


def prepared_change(state_context: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The change the ``prepare`` step stored on a request, or None."""
    ctx = state_context or {}
    for key in (PREPARED_CONTEXT_KEY, LEGACY_PREPARED_CONTEXT_KEY):
        value = ctx.get(key)
        if isinstance(value, dict) and value:
            return value
    return None


def open_requests(db: Session) -> list:
    from app.db import RequestModel

    return (
        db.query(RequestModel)
        .filter(RequestModel.type == WORKFLOW_ID)
        .filter(~RequestModel.status.in_(TERMINAL_STATUSES))
        .order_by(RequestModel.created_at.asc())
        .all()
    )


def count_open(db: Session) -> int:
    from app.db import RequestModel

    return (
        db.query(RequestModel)
        .filter(RequestModel.type == WORKFLOW_ID)
        .filter(~RequestModel.status.in_(TERMINAL_STATUSES))
        .count()
    )


def touched_rule_ids(db: Session) -> Dict[str, List[str]]:
    """``rule_id -> [request_id, ...]`` for rows an open request's prepared change will edit."""
    out: Dict[str, List[str]] = {}
    for req in open_requests(db):
        change = prepared_change(req.state_context) or {}
        seen: Set[str] = set()
        for diff in change.get("diffs") or []:
            rule_id = diff.get("rule_id")
            if rule_id and rule_id not in seen:
                seen.add(rule_id)
                out.setdefault(rule_id, []).append(req.id)
    return out
