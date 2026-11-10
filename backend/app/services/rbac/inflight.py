"""Open rbac_access_change requests, and which rules their prepared changes touch.

The workflow's ``prepare`` step stores the prepared change in the request's state
context under ``prepared_change`` (an ``AccessProposal`` dict). Rows a pending
change will edit get a marker in the grid, overlap warnings come from here, and
a reset is blocked while any of these requests is open.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

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


def securable_label(cells: Mapping[str, Any]) -> str:
    """A rule's or item's securable in words: ``table main.sales.orders``, ``workspace ws-dev``."""
    kind = " ".join(str(cells.get("securable_type") or "").replace("_", " ").split()).lower()
    uc = ".".join(p for p in (str(cells.get(k) or "").strip() for k in ("catalog", "schema_name", "object_name")) if p)
    name = uc or " ".join(
        p for p in (str(cells.get(k) or "").strip() for k in ("workspace_name", "object_name")) if p)
    return f"{kind} {name}".strip()


@dataclass(frozen=True)
class Touch:
    """An open request whose prepared change edits a rule."""

    request_id: str
    # What the request asks for, without the prerequisites it adds, e.g. ("table main.sales.orders",).
    objects: Tuple[str, ...]
    # Whether the edited rule is on one of those objects, rather than a prerequisite row
    # (USE_CATALOG / USE_SCHEMA on the containing catalog or schema).
    requested_row: bool


def touches(db: Session, *, exclude_request_id: Optional[str] = None) -> Dict[str, List[Touch]]:
    """``rule_id -> [Touch, ...]`` for rows an open request's prepared change will edit."""
    out: Dict[str, List[Touch]] = {}
    for req in open_requests(db):
        if exclude_request_id and req.id == exclude_request_id:
            continue
        change = prepared_change(req.state_context) or {}
        prerequisites = [p for p in change.get("added_prerequisites") or [] if isinstance(p, dict)]
        asked = [i for i in change.get("items") or [] if isinstance(i, dict) and i not in prerequisites]
        objects = tuple(dict.fromkeys(securable_label(i) for i in asked))
        wanted = {o.lower() for o in objects}
        seen: Set[str] = set()
        for diff in change.get("diffs") or []:
            rule_id = diff.get("rule_id")
            if not rule_id or rule_id in seen:
                continue
            seen.add(rule_id)
            row = securable_label(diff.get("after") or diff.get("before") or {})
            # A direct edit has no items: every row it changes is deliberate.
            out.setdefault(rule_id, []).append(
                Touch(request_id=req.id, objects=objects, requested_row=not asked or row.lower() in wanted))
    return out


def touched_rule_ids(db: Session) -> Dict[str, List[str]]:
    """``rule_id -> [request_id, ...]`` for rows an open request's prepared change will edit."""
    return {rule_id: [t.request_id for t in found] for rule_id, found in touches(db).items()}
