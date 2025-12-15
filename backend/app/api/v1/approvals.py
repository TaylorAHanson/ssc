"""
Approval API endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.request import ApprovalModel

router = APIRouter()


@router.get("/")
async def get_approvals(
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get all approvals, optionally filtered by status."""
    query = db.query(ApprovalModel)
    if status:
        query = query.filter(ApprovalModel.status == status)
    return query.all()


@router.get("/{approval_id}")
async def get_approval(
    approval_id: str,
    db: Session = Depends(get_db)
):
    """Get a specific approval by ID."""
    approval = db.query(ApprovalModel).filter(ApprovalModel.id == approval_id).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    return approval

