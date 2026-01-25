"""
Approval API endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional, List
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.request import ApprovalModel, RequestModel
from app.models.request import Approval, RequestType, ApprovalType

router = APIRouter()


@router.get("/", response_model=List[Approval])
async def get_approvals(
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get all approvals, optionally filtered by status."""
    query = db.query(ApprovalModel, RequestModel).join(RequestModel, ApprovalModel.request_id == RequestModel.id)
    
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
    db: Session = Depends(get_db)
):
    """Get a specific approval by ID."""
    result = db.query(ApprovalModel, RequestModel)\
        .join(RequestModel, ApprovalModel.request_id == RequestModel.id)\
        .filter(ApprovalModel.id == approval_id)\
        .first()
        
    if not result:
        raise HTTPException(status_code=404, detail="Approval not found")
        
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

