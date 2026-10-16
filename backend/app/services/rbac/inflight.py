"""Open rbac_access_change requests, and which rules their plans touch.

The workflow's ``plan`` step stores its proposal in the request's state context
under ``rbac_plan`` (an ``AccessProposal`` dict). Rows a pending plan will edit
get a marker in the grid, overlap warnings come from here, and a reset is
blocked while any of these requests is open.
"""
from __future__ import annotations

from typing import Dict, List, Set

from sqlalchemy.orm import Session

WORKFLOW_ID = "rbac_access_change"
TERMINAL_STATUSES = frozenset({"completed", "rejected", "failed"})
PLAN_CONTEXT_KEY = "rbac_plan"


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
    """``rule_id -> [request_id, ...]`` for rows an open request's plan will edit."""
    out: Dict[str, List[str]] = {}
    for req in open_requests(db):
        plan = (req.state_context or {}).get(PLAN_CONTEXT_KEY) or {}
        seen: Set[str] = set()
        for diff in plan.get("diffs") or []:
            rule_id = diff.get("rule_id")
            if rule_id and rule_id not in seen:
                seen.add(rule_id)
                out.setdefault(rule_id, []).append(req.id)
    return out
