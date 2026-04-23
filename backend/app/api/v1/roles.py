"""
Role mapping administration endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, ConfigDict
from app.db.session import get_db
from app.api.deps import get_current_user, require_role
from app.db.role_mapping import RoleMappingModel
from app.models.user import User

router = APIRouter()

class RoleMappingBase(BaseModel):
    external_role: str
    internal_role: str

class RoleMappingCreate(RoleMappingBase):
    pass

class RoleMappingUpdate(RoleMappingBase):
    pass

class RoleMapping(RoleMappingBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

@router.get("/me", response_model=User)
async def read_users_me(current_user: User = Depends(get_current_user)):
    """Get current user and their calculated roles."""
    return current_user

@router.get("/mapping", response_model=List[RoleMapping], dependencies=[Depends(require_role("Platform Admin"))])
async def read_role_mappings(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db)
):
    """List all role mappings (Platform Admin only)."""
    mappings = db.query(RoleMappingModel).offset(skip).limit(limit).all()
    return mappings

@router.post("/mapping", response_model=RoleMapping, dependencies=[Depends(require_role("Platform Admin"))])
async def create_role_mapping(
    mapping_in: RoleMappingCreate,
    db: Session = Depends(get_db)
):
    """Create a new role mapping (Platform Admin only)."""
    new_mapping = RoleMappingModel(
        external_role=mapping_in.external_role,
        internal_role=mapping_in.internal_role
    )
    db.add(new_mapping)
    db.commit()
    db.refresh(new_mapping)
    return new_mapping

@router.put("/mapping/{mapping_id}", response_model=RoleMapping, dependencies=[Depends(require_role("Platform Admin"))])
async def update_role_mapping(
    mapping_id: int,
    mapping_in: RoleMappingUpdate,
    db: Session = Depends(get_db)
):
    """Update an existing role mapping (Platform Admin only)."""
    mapping = db.query(RoleMappingModel).filter(RoleMappingModel.id == mapping_id).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="Role mapping not found")
        
    mapping.external_role = mapping_in.external_role
    mapping.internal_role = mapping_in.internal_role
    
    db.commit()
    db.refresh(mapping)
    return mapping

@router.delete("/mapping/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_role("Platform Admin"))])
async def delete_role_mapping(
    mapping_id: int,
    db: Session = Depends(get_db)
):
    """Delete a role mapping (Platform Admin only)."""
    mapping = db.query(RoleMappingModel).filter(RoleMappingModel.id == mapping_id).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="Role mapping not found")
        
    db.delete(mapping)
    db.commit()
