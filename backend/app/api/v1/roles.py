"""
Role mapping administration endpoints, plus the read-only role/capability grid.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, List, Optional
from pydantic import BaseModel, ConfigDict
from app.db.session import get_db
from app.api.deps import get_current_user, require_capability
from app.core import capabilities
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


class CurrentUser(User):
    """The caller, with the capability ids their roles hold (drives the UI's nav and routes)."""
    capabilities: List[str] = []


class RoleInfo(BaseModel):
    key: str
    label: str
    description: str
    mapped_identities: List[str]


class SettingRoles(BaseModel):
    key: str
    label: str
    current_roles: List[str]


class CapabilityInfo(BaseModel):
    id: str
    label: str
    description: str
    group: str
    roles: List[str]
    also_from_setting: Optional[SettingRoles] = None
    note: Optional[str] = None


class CapabilityGrid(BaseModel):
    roles: List[RoleInfo]
    groups: List[str]
    capabilities: List[CapabilityInfo]
    can_manage_roles: bool


@router.get("/me", response_model=CurrentUser)
async def read_users_me(current_user: User = Depends(get_current_user)):
    """Get current user, their calculated roles and the capabilities those roles hold."""
    return CurrentUser(**current_user.model_dump(), capabilities=current_user.capability_ids())


@router.get("/capabilities", response_model=CapabilityGrid)
def read_capability_grid(
    current_user: User = Depends(require_capability("roles.view")),
    db: Session = Depends(get_db),
):
    """Every role (with the identities mapped to it) and every capability with the roles that hold it."""
    mapped: Dict[str, List[str]] = {label: [] for label in capabilities.ROLE_LABELS}
    for row in db.query(RoleMappingModel).order_by(RoleMappingModel.external_role).all():
        label = capabilities.canonical_role(row.internal_role)
        bucket = mapped.setdefault(label, [])
        if row.external_role not in bucket:
            bucket.append(row.external_role)

    roles = [
        RoleInfo(key=r.key, label=r.label, description=r.description, mapped_identities=mapped.get(r.label, []))
        for r in capabilities.ROLES
    ]
    caps = []
    for cap in capabilities.all_capabilities():
        extra = None
        if cap.also_from_setting:
            extra = SettingRoles(
                key=cap.also_from_setting,
                label=cap.also_from_setting_label or cap.also_from_setting,
                current_roles=capabilities.setting_roles(cap.id),
            )
        caps.append(CapabilityInfo(
            id=cap.id, label=cap.label, description=cap.description, group=cap.group,
            roles=[capabilities.SUPER_ADMIN] + capabilities.roles_for(cap.id, include_setting=False),
            also_from_setting=extra, note=cap.note,
        ))
    return CapabilityGrid(
        roles=roles,
        groups=list(capabilities.GROUP_ORDER),
        capabilities=caps,
        can_manage_roles=current_user.can("roles.manage"),
    )


def _clean(mapping_in: RoleMappingBase) -> Dict[str, str]:
    external = mapping_in.external_role.strip()
    internal = capabilities.canonical_role(mapping_in.internal_role)
    if not external or not internal:
        raise HTTPException(status_code=422, detail="Both the group or identity and the app role are required.")
    return {"external_role": external, "internal_role": internal}


@router.get("/mapping", response_model=List[RoleMapping], dependencies=[Depends(require_capability("roles.view"))])
def read_role_mappings(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db)
):
    """List all role mappings."""
    mappings = db.query(RoleMappingModel).offset(skip).limit(limit).all()
    return mappings

@router.post("/mapping", response_model=RoleMapping, dependencies=[Depends(require_capability("roles.manage"))])
def create_role_mapping(
    mapping_in: RoleMappingCreate,
    db: Session = Depends(get_db)
):
    """Create a new role mapping."""
    new_mapping = RoleMappingModel(**_clean(mapping_in))
    db.add(new_mapping)
    db.commit()
    db.refresh(new_mapping)
    return new_mapping

@router.put("/mapping/{mapping_id}", response_model=RoleMapping, dependencies=[Depends(require_capability("roles.manage"))])
def update_role_mapping(
    mapping_id: int,
    mapping_in: RoleMappingUpdate,
    db: Session = Depends(get_db)
):
    """Update an existing role mapping."""
    mapping = db.query(RoleMappingModel).filter(RoleMappingModel.id == mapping_id).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="Role mapping not found")

    cleaned = _clean(mapping_in)
    mapping.external_role = cleaned["external_role"]
    mapping.internal_role = cleaned["internal_role"]
    
    db.commit()
    db.refresh(mapping)
    return mapping

@router.delete("/mapping/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_capability("roles.manage"))])
def delete_role_mapping(
    mapping_id: int,
    db: Session = Depends(get_db)
):
    """Delete a role mapping."""
    mapping = db.query(RoleMappingModel).filter(RoleMappingModel.id == mapping_id).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="Role mapping not found")
        
    db.delete(mapping)
    db.commit()
