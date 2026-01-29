"""
User management endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, ConfigDict
from app.db.session import get_db
from app.api.deps import get_current_user, require_role
from app.db.user import UserModel, RoleModel, user_roles
from datetime import datetime

router = APIRouter()

# --- Pydantic Models ---
class RoleBase(BaseModel):
    name: str
    description: Optional[str] = None

class Role(RoleBase):
    id: str
    model_config = ConfigDict(from_attributes=True)

class UserBase(BaseModel):
    email: str
    full_name: Optional[str] = None
    is_active: bool = True

class UserCreate(UserBase):
    role_ids: List[str] = []

class User(UserBase):
    id: str
    roles: List[Role] = []
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class UserRoleUpdate(BaseModel):
    role_ids: List[str]

# --- Endpoints ---

@router.get("/me", response_model=User)
async def read_users_me(current_user: UserModel = Depends(get_current_user)):
    """Get current user."""
    return current_user

@router.get("")
@router.get("/", response_model=List[User], dependencies=[Depends(require_role("platform_admin"))])
async def read_users(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db)
):
    """List all users (Admin only)."""
    users = db.query(UserModel).offset(skip).limit(limit).all()
    return users

@router.post("")
@router.post("/", response_model=User, dependencies=[Depends(require_role("platform_admin"))])
async def create_user(
    user_in: UserCreate,
    db: Session = Depends(get_db)
):
    """Create a new user (Admin only)."""
    # Check if user exists
    db_user = db.query(UserModel).filter(UserModel.email == user_in.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="User with this email already exists")
    
    import uuid
    new_user = UserModel(
        id=str(uuid.uuid4()),
        email=user_in.email,
        full_name=user_in.full_name,
        is_active=user_in.is_active
    )
    
    # Assign roles if provided
    if user_in.role_ids:
        roles = db.query(RoleModel).filter(RoleModel.id.in_(user_in.role_ids)).all()
        if len(roles) != len(user_in.role_ids):
            raise HTTPException(status_code=400, detail="One or more role IDs are invalid")
        new_user.roles = roles
        
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@router.get("/roles", response_model=List[Role])
async def read_roles(db: Session = Depends(get_db)):
    """List all available roles."""
    roles = db.query(RoleModel).all()
    return roles

@router.put("/{user_id}/roles", response_model=User, dependencies=[Depends(require_role("platform_admin"))])
async def update_user_roles(
    user_id: str,
    role_update: UserRoleUpdate,
    db: Session = Depends(get_db)
):
    """Update a user's roles (Admin only)."""
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # Verify roles exist
    roles = db.query(RoleModel).filter(RoleModel.id.in_(role_update.role_ids)).all()
    if len(roles) != len(role_update.role_ids):
        raise HTTPException(status_code=400, detail="One or more role IDs are invalid")
        
    # Update roles
    user.roles = roles
    db.commit()
    db.refresh(user)
    return user
