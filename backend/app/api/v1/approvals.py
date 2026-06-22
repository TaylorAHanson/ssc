"""
Approval API endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional, List
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db import ApprovalModel, RequestModel
from app.models.request import Approval, ApprovalType
from app.api.deps import get_current_user
from app.models.user import User
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

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


def _map_approval(approval_model: ApprovalModel, request_model: RequestModel) -> Approval:
    """Map an ApprovalModel + RequestModel to an Approval Pydantic response."""
    return Approval(
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
    )


@router.get("", response_model=List[Approval])
async def get_approvals(
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
    
    # Build list of role-based approval types the user can see
    allowed_types = []
    if current_user.has_role("Platform Admin"):
        # Platform Admins can see all approvals
        allowed_types.extend(["platform_admin", "manager", "data_owner", "security", "security_admin", "finance_admin", "governance_admin"])
    if current_user.has_role("Governance Admin"):
        allowed_types.append("governance_admin")
    if current_user.has_role("Security Admin"):
        allowed_types.append("security")
        allowed_types.append("security_admin")
    if current_user.has_role("Finance Admin"):
        allowed_types.append("finance_admin")
        
    query = query.filter(
        (ApprovalModel.assigned_to_email == current_user.email) | 
        (ApprovalModel.delegated_to_email == current_user.email) |
        (ApprovalModel.assigned_to_role.in_(current_user.entitlements)) |
        (ApprovalModel.approval_type.in_(allowed_types))
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
    return [_map_approval(am, rm) for am, rm in results]


@router.get("/{approval_id}", response_model=Approval)
async def get_approval(
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
    
    # Check permission
    allowed_types = []
    if current_user.has_role("Platform Admin"):
        # Platform Admins can see all approvals
        allowed_types.extend(["platform_admin", "manager", "data_owner", "security", "security_admin", "finance_admin", "governance_admin"])
    if current_user.has_role("Governance Admin"): allowed_types.append("governance_admin")
    if current_user.has_role("Security Admin"): 
        allowed_types.append("security")
        allowed_types.append("security_admin")
    if current_user.has_role("Finance Admin"): allowed_types.append("finance_admin")
        
    is_assigned = approval_model.assigned_to_email == current_user.email
    is_delegated = approval_model.delegated_to_email == current_user.email
    is_role_assigned = approval_model.assigned_to_role in current_user.entitlements
    is_role_based = approval_model.approval_type in allowed_types
    
    if not (is_assigned or is_delegated or is_role_assigned or is_role_based):
        raise HTTPException(status_code=403, detail="Not authorized to view this approval")
        
    return _map_approval(approval_model, request_model)


