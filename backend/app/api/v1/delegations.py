"""
Delegation API endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional
from sqlalchemy.orm import Session
from datetime import datetime
import uuid
from app.db.session import get_db
from app.db.request import DelegationModel
from app.models.request import Delegation, DelegationCreate
from app.api.deps import get_current_user
from app.db.user import UserModel

router = APIRouter()

@router.get("", response_model=List[Delegation])
async def get_delegations(
    delegator_email: Optional[str] = None,
    delegatee_email: Optional[str] = None,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all delegations, optionally filtered by delegator or delegatee."""
    query = db.query(DelegationModel)
    
    # Non-admins can only see their own delegations
    if not current_user.has_role("platform_admin"):
        query = query.filter(
            (DelegationModel.delegator_email == current_user.email) | 
            (DelegationModel.delegatee_email == current_user.email)
        )
    else:
        # Admins can filter by email if they want
        if delegator_email:
            query = query.filter(DelegationModel.delegator_email == delegator_email)
        if delegatee_email:
            query = query.filter(DelegationModel.delegatee_email == delegatee_email)
    
    results = query.all()
    # ... (rest of the mapping logic)
    
    return [
        Delegation(
            id=d.id,
            delegator_email=d.delegator_email,
            delegatee_email=d.delegatee_email,
            start_date=d.start_date,
            end_date=d.end_date,
            is_active=d.is_active,
            created_at=d.created_at,
            updated_at=d.updated_at
        ) for d in results
    ]

@router.post("", response_model=Delegation)
async def create_delegation(
    delegation_in: DelegationCreate,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new delegation."""
    delegator_email = current_user.email
    
    new_delegation = DelegationModel(
        id=f"del-{uuid.uuid4().hex[:8]}",
        delegator_email=delegator_email,
        delegatee_email=delegation_in.delegatee_email,
        start_date=delegation_in.start_date,
        end_date=delegation_in.end_date,
        is_active=True
    )
    
    db.add(new_delegation)
    db.commit()
    db.refresh(new_delegation)
    
    return Delegation(
        id=new_delegation.id,
        delegator_email=new_delegation.delegator_email,
        delegatee_email=new_delegation.delegatee_email,
        start_date=new_delegation.start_date,
        end_date=new_delegation.end_date,
        is_active=new_delegation.is_active,
        created_at=new_delegation.created_at,
        updated_at=new_delegation.updated_at
    )

@router.delete("/{delegation_id}")
async def delete_delegation(
    delegation_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete (deactivate) a delegation."""
    delegation = db.query(DelegationModel).filter(DelegationModel.id == delegation_id).first()
    if not delegation:
        raise HTTPException(status_code=404, detail="Delegation not found")
    
    # Check permission
    if not current_user.has_role("platform_admin") and delegation.delegator_email != current_user.email:
        raise HTTPException(status_code=403, detail="Not authorized to delete this delegation")
    
    db.delete(delegation)
    db.commit()
    
    return {"status": "deleted"}
