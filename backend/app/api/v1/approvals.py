"""
Approval API endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Iterable, Optional, List
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db import ApprovalModel, EventModel, RequestModel, WorkflowModel
from app.models.request import Approval, ApprovalType, StepReport
from app.workflows.spec import STEP_REPORT_FACT
from app.workflows.graphs import editable_states
from app.api.deps import get_current_user
from app.models.user import User
from app.services.approval_scope import (
    allowed_approval_types,
    approval_visibility_filter,
    in_group,
)
from app.api.v1.requests import is_own_request, self_approval_blocked
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


class InboxApproval(Approval):
    """An approval as the current user sees it in the inbox."""
    # The current user filed this request.
    isOwnRequest: bool = False
    # ...and APPROVALS_DENY_SELF_APPROVAL is on: they may reject (withdraw) it, not approve it.
    selfApprovalBlocked: bool = False
    # A Platform Admin may Edit & Restart the request from this gate.
    canEditParameters: bool = False
    # Display name of the workflow behind requestType; None when no workflow row has one.
    workflowName: Optional[str] = None

# Keys excluded from the editable workflow parameters view.
# These are internal/system fields that should not surface to approvers.
_EXCLUDED_PARAM_KEYS = {"requested_by", "requested_by_email"}


def _get_workflow_params(state_context: dict | None) -> dict:
    """Return a filtered view of state_context safe for approver consumption.
    
    Excludes internal tracking fields (requested_by, requested_by_email,
    and any key starting with '_').
    """
    if not state_context:
        return {}
    return {
        k: v for k, v in state_context.items()
        if k not in _EXCLUDED_PARAM_KEYS and not k.startswith("_")
    }


def _load_step_reports(db: Session, request_ids: Iterable[str]) -> Dict[str, List[StepReport]]:
    """Reports earlier steps produced, per request: the latest per step, in step order.

    One query for the whole inbox. A step re-run (Edit & Restart, a retry)
    records a fresh report, and only its newest one is shown.
    """
    ids = list({rid for rid in request_ids if rid})
    if not ids:
        return {}
    rows = (
        db.query(EventModel)
        .filter(EventModel.request_id.in_(ids), EventModel.event_type == STEP_REPORT_FACT)
        .order_by(EventModel.created_at.asc())
        .all()
    )
    latest: Dict[str, Dict[str, StepReport]] = {}
    for row in rows:
        data = row.event_data or {}
        markdown = data.get("markdown")
        if not isinstance(markdown, str):
            continue
        step = str(data.get("step") or "")
        per_request = latest.setdefault(row.request_id, {})
        per_request.pop(step, None)  # re-insert so order follows the newest run
        per_request[step] = StepReport(
            step=step,
            tool=str(data.get("tool") or ""),
            title=str(data.get("title") or step),
            markdown=markdown,
            createdAt=row.created_at,
        )
    return {rid: list(by_step.values()) for rid, by_step in latest.items()}


def _load_workflow_names(db: Session, request_types: Iterable[str]) -> Dict[str, str]:
    """Each request type's workflow display name, for the inbox's workflow filter.

    One query for the whole inbox. A type can be served by several rows (a
    draft fork, or a workflow whose ``key`` is the type): a ``request_type``
    match beats a ``key`` match, and published beats draft. Types with no named
    row are left out; the UI falls back to the type itself.
    """
    wanted = {t for t in request_types if t}
    if not wanted:
        return {}
    rows = (
        db.query(WorkflowModel.key, WorkflowModel.request_type, WorkflowModel.name, WorkflowModel.status)
        .filter(or_(WorkflowModel.request_type.in_(wanted), WorkflowModel.key.in_(wanted)))
        .all()
    )
    best: Dict[str, tuple] = {}
    for key, request_type, name, status in rows:
        name = (name or "").strip()
        if not name:
            continue
        for matched, by_type in ((request_type, True), (key, False)):
            if matched not in wanted:
                continue
            rank = (by_type, status == "published")
            if matched not in best or rank > best[matched][0]:
                best[matched] = (rank, name)
    return {t: name for t, (_, name) in best.items()}


def _load_approver_labels(db: Session, rows: Iterable[tuple]) -> Dict[str, str]:
    """``approval id -> who approves`` for approvals whose gate takes its group from the access rule type.

    Read from each request type's governing spec (a published workflow, else the
    catalog), once per type in the inbox.
    """
    from app.services.rbac.requests import approver_label
    from app.workflows.graphs import published_graph_spec
    from app.workflows.graphs.specs import SPECS

    gate_types: Dict[str, set] = {}
    labels: Dict[str, str] = {}
    for approval, request in rows:
        if request.type not in gate_types:
            spec = published_graph_spec(db, request.type) or SPECS.get(request.type) or {}
            gate_types[request.type] = {
                stage.get("type") for stage in spec.get("stages") or []
                if isinstance(stage, dict) and stage.get("kind") == "gate"
                and isinstance(stage.get("approver"), dict)
                and stage["approver"].get("source") == "access_rule_type"
            }
        if approval.approval_type in gate_types[request.type]:
            labels[approval.id] = approver_label(request.state_context, approval.assigned_to_role)
    return labels


def _map_approval(
    approval_model: ApprovalModel,
    request_model: RequestModel,
    reports: Optional[List[StepReport]] = None,
    current_user: Optional[User] = None,
    workflow_name: Optional[str] = None,
    approver_label: Optional[str] = None,
) -> InboxApproval:
    """Map an ApprovalModel + RequestModel to an Approval Pydantic response."""
    requester = request_model.requester_email
    return InboxApproval(
        id=approval_model.id,
        requestId=approval_model.request_id,
        requestTitle=request_model.title,
        requestType=request_model.type,
        approvalType=approval_model.approval_type,
        # ``requested_by`` (display name) may be null for approvals created by the
        # workflow poller (which only records ``requested_by_email``). Fall back to
        # the email so the required ``requestedBy: str`` field never gets None —
        # otherwise serializing this row raises and 500s the whole inbox.
        requestedBy=approval_model.requested_by or approval_model.requested_by_email or "",
        requestedByEmail=approval_model.requested_by_email or "",
        assignedToEmail=approval_model.assigned_to_email,
        assignedToRole=approval_model.assigned_to_role,
        approvedBy=approval_model.approved_by,
        approvedAt=approval_model.approved_at,
        rejectedBy=approval_model.rejected_by,
        rejectedAt=approval_model.rejected_at,
        status=approval_model.status,
        createdAt=approval_model.created_at,
        updatedAt=approval_model.updated_at,
        rejectionNote=approval_model.rejection_note,
        delegatedTo=approval_model.delegated_to,
        delegatedToEmail=approval_model.delegated_to_email,
        supersededNote=approval_model.superseded_note,
        requestConversation=request_model.conversation,
        workflowParameters=_get_workflow_params(request_model.state_context),
        # Manual-task items carry the work description; without it the inbox row
        # is an unexplained "something is waiting on you".
        instructions=approval_model.instructions,
        dueAt=approval_model.due_at,
        reports=reports or [],
        isOwnRequest=bool(current_user) and is_own_request(requester, current_user),
        selfApprovalBlocked=bool(current_user) and self_approval_blocked(requester, current_user),
        canEditParameters=(approval_model.approval_type == "platform_admin"
                           and bool(editable_states(request_model.type))),
        workflowName=workflow_name,
        approverLabel=approver_label,
    )


@router.get("", response_model=List[InboxApproval])
def get_approvals(
    status: Optional[str] = None,
    skip: int = 0,
    limit: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get approvals, filtered by user involvement if not an admin.

    ``skip``/``limit`` are optional; when ``limit`` is omitted the full filtered
    set is returned (legacy behavior). Pass them to page through large inboxes
    instead of materializing every approval at once.
    """
    query = db.query(ApprovalModel, RequestModel).join(RequestModel, ApprovalModel.request_id == RequestModel.id)

    query = query.filter(
        approval_visibility_filter(
            current_user.email,
            roles=current_user.roles,
            entitlements=current_user.entitlements,
        )
    )
    
    if status:
        query = query.filter(ApprovalModel.status == status)

    # Newest first for stable pagination ordering.
    query = query.order_by(ApprovalModel.created_at.desc())
    if skip:
        query = query.offset(skip)
    if limit is not None:
        query = query.limit(limit)

    results = query.all()
    reports = _load_step_reports(db, (am.request_id for am, _ in results))
    names = _load_workflow_names(db, (rm.type for _, rm in results))
    labels = _load_approver_labels(db, results)
    return [
        _map_approval(am, rm, reports.get(am.request_id), current_user, names.get(rm.type), labels.get(am.id))
        for am, rm in results
    ]


@router.get("/{approval_id}", response_model=InboxApproval)
def get_approval(
    approval_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific approval by ID, with permission check."""
    result = db.query(ApprovalModel, RequestModel)\
        .join(RequestModel, ApprovalModel.request_id == RequestModel.id)\
        .filter(ApprovalModel.id == approval_id)\
        .first()
        
    if not result:
        raise HTTPException(status_code=404, detail="Approval not found")
        
    approval_model, request_model = result
    
    # Same rules as the inbox list above, from the shared table rather than a
    # second hand-maintained copy: this one had already drifted (it predated
    # ``manual_task``, so a manual task nobody was assigned 403'd here while the
    # list endpoint showed it).
    allowed_types = allowed_approval_types(current_user.roles)

    is_assigned = approval_model.assigned_to_email == current_user.email
    is_delegated = approval_model.delegated_to_email == current_user.email
    is_role_assigned = in_group(current_user.entitlements, approval_model.assigned_to_role)
    is_role_based = approval_model.approval_type in allowed_types
    
    if not (is_assigned or is_delegated or is_role_assigned or is_role_based):
        raise HTTPException(status_code=403, detail="Not authorized to view this approval")
        
    reports = _load_step_reports(db, [approval_model.request_id])
    names = _load_workflow_names(db, [request_model.type])
    labels = _load_approver_labels(db, [result])
    return _map_approval(approval_model, request_model, reports.get(approval_model.request_id), current_user,
                         names.get(request_model.type), labels.get(approval_model.id))


