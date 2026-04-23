"""
API dependencies.
"""
from fastapi import Depends, HTTPException, status, Header, Request
from typing import Generator, Optional, List, Dict
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.core.config import settings
import uuid
import logging
from app.models.user import User
from app.db.role_mapping import RoleMappingModel
from app.providers.github.client import GitHubProvider
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import DatabricksError

logger = logging.getLogger(__name__)

MOCK_USER_EMAIL = "admin@qualcomm.com"

def _get_user_entitlements(user_email: str) -> List[str]:
    """Fetch SCIM entitlements (groups/roles) using Databricks SDK."""
    entitlements = [user_email]
    
    # Local dev mock fallback: return 'users' per instructions
    if user_email == MOCK_USER_EMAIL and settings.ENVIRONMENT != "production":
        entitlements.append("users")
        return entitlements

    try:
        w = WorkspaceClient()
        me = w.current_user.me()
        
        if getattr(me, "groups", None):
            for group in me.groups:
                if getattr(group, "display", None):
                    entitlements.append(group.display)
                    
        if getattr(me, "roles", None):
            for role in me.roles:
                if getattr(role, "value", None):
                    entitlements.append(role.value)
                    
    except DatabricksError as e:
        logger.warning(f"Failed to fetch Databricks entitlements for {user_email}: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error fetching entitlements: {e}")

    return entitlements

def _calculate_roles(db: Session, entitlements: List[str]) -> List[str]:
    """Calculate internal application roles based on role mappings."""
    roles = set()
    
    if not entitlements:
        return []
        
    mappings = db.query(RoleMappingModel).filter(
        RoleMappingModel.external_role.in_(entitlements)
    ).all()
    
    for mapping in mappings:
        roles.add(mapping.internal_role)
            
    return list(roles)

def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    x_dev_role_override: Optional[str] = Header(None, alias="X-Dev-Role-Override")
) -> User:
    """
    Get the current authenticated user and calculate roles.
    """
    user_email = None
    user_name = None
    
    if hasattr(request.state, "user") and request.state.user.get("email"):
        user_email = request.state.user["email"]
        user_name = request.state.user.get("username", user_email)
        
    if not user_email or user_email == settings.MOCK_USER_EMAIL:
         user_email = MOCK_USER_EMAIL
         user_name = "System Admin"
         
    entitlements = _get_user_entitlements(user_email)
    calculated_roles = _calculate_roles(db, entitlements)
    
    # Local dev mock fallback: if 'users' in entitlements and local dev, ensure 'Platform Admin' or 'User' is present.
    if settings.ENVIRONMENT != "production" and "users" in entitlements and not calculated_roles:
        # Fallback to giving Platform Admin to the mock user if no DB seeding occurred yet
        calculated_roles = ["Platform Admin"]
    
    # DEV FEATURE: Role Override
    if x_dev_role_override and settings.ENVIRONMENT != "production":
        logger.info(f"DEV: Overriding role for user {user_email} to {x_dev_role_override}")
        calculated_roles = [x_dev_role_override]
            
    user = User(
        id=user_email,
        email=user_email,
        full_name=user_name,
        entitlements=entitlements,
        roles=calculated_roles
    )
    
    return user

def require_role(role_name: str):
    def checker(user: User = Depends(get_current_user)):
        if not user.has_role(role_name):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires role: {role_name}"
            )
        return user
    return checker

def require_any_role(role_names: List[str]):
    def checker(user: User = Depends(get_current_user)):
        if not any(user.has_role(role) for role in role_names):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires one of these roles: {', '.join(role_names)}"
            )
        return user
    return checker

async def get_github_provider() -> GitHubProvider:
    """
    Dependency to get a GitHub provider instance.
    """
    async with GitHubProvider(
        token=settings.GITHUB_TOKEN,
        org=settings.GITHUB_ORG
    ) as github:
        yield github
