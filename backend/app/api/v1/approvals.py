"""
Approval API endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional, List
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.request import ApprovalModel, RequestModel
from app.models.request import Approval, RequestType, ApprovalType
from app.api.deps import get_current_user
from app.db.user import UserModel

router = APIRouter()


@router.get("/", response_model=List[Approval])
async def get_approvals(
    status: Optional[str] = None,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get approvals, filtered by user involvement if not an admin."""
    query = db.query(ApprovalModel, RequestModel).join(RequestModel, ApprovalModel.request_id == RequestModel.id)
    
    # Filter by user involvement if not a platform admin
    if not current_user.has_role("platform_admin"):
        query = query.filter(
            (ApprovalModel.requested_by_email == current_user.email) | 
            (ApprovalModel.delegated_to_email == current_user.email)
        )
    
    if status:
        query = query.filter(ApprovalModel.status == status)
        
    results = query.all()
    
    # Map to Pydantic model
    approvals = []
    for approval_model, request_model in results:
        approvals.append(Approval(
            id=approval_model.id,
            requestId=approval_model.request_id,
            requestTitle=request_model.title,
            requestType=request_model.type,
            approvalType=approval_model.approval_type,
            requestedBy=approval_model.requested_by,
            requestedByEmail=approval_model.requested_by_email or "",
            status=approval_model.status,
            createdAt=approval_model.created_at,
            updatedAt=approval_model.updated_at,
            rejectionNote=approval_model.rejection_note,
            delegatedTo=approval_model.delegated_to,
            delegatedToEmail=approval_model.delegated_to_email,
            requestConversation=request_model.conversation
        ))
        
    return approvals


@router.get("/{approval_id}", response_model=Approval)
async def get_approval(
    approval_id: str,
    current_user: UserModel = Depends(get_current_user),
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
    if not current_user.has_role("platform_admin"):
        if approval_model.requested_by_email != current_user.email and \
           approval_model.delegated_to_email != current_user.email:
            raise HTTPException(status_code=403, detail="Not authorized to view this approval")
        
    approval_model, request_model = result
    
    return Approval(
        id=approval_model.id,
        requestId=approval_model.request_id,
        requestTitle=request_model.title,
        requestType=request_model.type,
        approvalType=approval_model.approval_type,
        requestedBy=approval_model.requested_by,
        requestedByEmail=approval_model.requested_by_email or "",
        status=approval_model.status,
        createdAt=approval_model.created_at,
        updatedAt=approval_model.updated_at,
        rejectionNote=approval_model.rejection_note,
        delegatedTo=approval_model.delegated_to,
        delegatedToEmail=approval_model.delegated_to_email
    )

