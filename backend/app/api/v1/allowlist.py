import logging
from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
import uuid

from app.api import deps
from app.db.allowlist import AllowlistModel
from app.db.user import UserModel
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

# --- Schemas ---

class AllowlistCreate(BaseModel):
    resource_id: str = Field(..., description="Normalized Databricks ID or path of the resource")
    resource_type: str = Field(..., description="Enum: app, notebook, dashboard, genie_space, etc.")
    workspace: str = Field(..., description="The workspace ID or name where this resource lives")
    justification: str = Field(..., description="Reason for the exception")
    status: str = Field(default="pending", description="Status: pending, approved, rejected")
    request_id: str | None = Field(default=None, description="FK to requests.id")
    expires_at: datetime | None = Field(default=None, description="Date when the exception naturally revokes")

class AllowlistUpdate(BaseModel):
    justification: str | None = None
    status: str | None = None
    expires_at: datetime | None = None

class AllowlistResponse(BaseModel):
    id: str
    resource_id: str
    resource_type: str
    workspace: str
    justification: str
    status: str
    request_id: str | None = None
    expires_at: datetime | None = None
    approved_by: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# --- Endpoints ---

@router.get("", response_model=List[AllowlistResponse])
@router.get("/", response_model=List[AllowlistResponse])
def get_allowlist(
    db: Session = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    workspace: str | None = None,
    resource_type: str | None = None,
    status: str | None = None,
    current_user: UserModel = Depends(deps.get_current_user),
) -> Any:
    """
    Retrieve allowlist entries.
    """
    query = db.query(AllowlistModel)
    
    if workspace:
        query = query.filter(AllowlistModel.workspace == workspace)
    if resource_type:
        query = query.filter(AllowlistModel.resource_type == resource_type)
    if status:
        query = query.filter(AllowlistModel.status == status)
        
    entries = query.offset(skip).limit(limit).all()
    return entries

@router.get("/{id}", response_model=AllowlistResponse)
def get_allowlist_entry(
    id: str,
    db: Session = Depends(deps.get_db),
    current_user: UserModel = Depends(deps.get_current_user),
) -> Any:
    """
    Get a specific allowlist entry by ID.
    """
    entry = db.query(AllowlistModel).filter(AllowlistModel.id == id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Allowlist entry not found")
    return entry

@router.post("", response_model=AllowlistResponse)
@router.post("/", response_model=AllowlistResponse)
def create_allowlist_entry(
    *,
    db: Session = Depends(deps.get_db),
    entry_in: AllowlistCreate,
    current_user: UserModel = Depends(deps.require_role("platform_admin")),
) -> Any:
    """
    Create new allowlist entry. Only accessible to platform admins.
    """
    # Create the db model
    db_obj = AllowlistModel(
        id=str(uuid.uuid4()),
        resource_id=entry_in.resource_id,
        resource_type=entry_in.resource_type,
        workspace=entry_in.workspace,
        justification=entry_in.justification,
        status=entry_in.status,
        request_id=entry_in.request_id,
        expires_at=entry_in.expires_at
    )
    
    # If immediately approved, record who did it
    if entry_in.status == "approved":
        db_obj.approved_by = current_user.email
        
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

@router.put("/{id}", response_model=AllowlistResponse)
def update_allowlist_entry(
    *,
    db: Session = Depends(deps.get_db),
    id: str,
    entry_in: AllowlistUpdate,
    current_user: UserModel = Depends(deps.require_role("platform_admin")),
) -> Any:
    """
    Update an allowlist entry. Only accessible to platform admins.
    """
    entry = db.query(AllowlistModel).filter(AllowlistModel.id == id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Allowlist entry not found")

    # Update fields
    if entry_in.justification is not None:
        entry.justification = entry_in.justification
        
    if entry_in.expires_at is not None:
        entry.expires_at = entry_in.expires_at

    # Handle status changes
    if entry_in.status is not None and entry_in.status != entry.status:
        entry.status = entry_in.status
        if entry_in.status == "approved":
            entry.approved_by = current_user.email
        elif entry_in.status in ["rejected", "pending"]:
            entry.approved_by = None

    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry

@router.delete("/{id}")
def delete_allowlist_entry(
    *,
    db: Session = Depends(deps.get_db),
    id: str,
    current_user: UserModel = Depends(deps.require_role("platform_admin")),
) -> Any:
    """
    Delete an allowlist entry. Only accessible to platform admins.
    """
    entry = db.query(AllowlistModel).filter(AllowlistModel.id == id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Allowlist entry not found")
        
    db.delete(entry)
    db.commit()
    return {"success": True}
