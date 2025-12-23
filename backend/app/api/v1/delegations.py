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

router = APIRouter()

@router.get("/", response_model=List[Delegation])
async def get_delegations(
    delegator_email: Optional[str] = None,
    delegatee_email: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get all delegations, optionally filtered by delegator or delegatee."""
    query = db.query(DelegationModel)
    if delegator_email:
        query = query.filter(DelegationModel.delegator_email == delegator_email)
    if delegatee_email:
        query = query.filter(DelegationModel.delegatee_email == delegatee_email)
    
    results = query.all()
    
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

@router.post("/", response_model=Delegation)
async def create_delegation(
    delegation_in: DelegationCreate,
    db: Session = Depends(get_db)
):
    """Create a new delegation."""
    # In a real app, we'd get the delegator email from the auth token
    # For this demo, we'll hardcode it to a mock admin user
    delegator_email = "admin@qualcomm.com"
    
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
    db: Session = Depends(get_db)
):
    """Delete (deactivate) a delegation."""
    delegation = db.query(DelegationModel).filter(DelegationModel.id == delegation_id).first()
    if not delegation:
        raise HTTPException(status_code=404, detail="Delegation not found")
    
    db.delete(delegation)
    db.commit()
    
    return {"status": "deleted"}

