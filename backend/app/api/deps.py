"""
API dependencies.
"""
from fastapi import Depends, HTTPException, status, Header
from typing import Generator, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.db.session import get_db
from app.core.config import settings
import uuid
import logging
from app.db.user import UserModel, RoleModel
from app.providers.github.client import GitHubProvider

logger = logging.getLogger(__name__)

# Mock user for development - in production this would verify JWT/OAuth
from fastapi import Request

MOCK_USER_EMAIL = "admin@qualcomm.com"

def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    x_dev_role_override: Optional[str] = Header(None, alias="X-Dev-Role-Override")
) -> UserModel:
    """
    Get the current authenticated user.
    Prioritizes user from Databricks headers (via middleware), 
    falls back to mock admin for local dev.
    """
    user_email = None
    
    # Check middleware state first (Databricks Apps)
    if hasattr(request.state, "user") and request.state.user.get("email"):
        user_email = request.state.user["email"]
        logger.debug(f"get_current_user: Found user in request state: {user_email}")
        
    # Fallback to mock user if nothing in state (Local Dev)
    if not user_email or user_email == settings.MOCK_USER_EMAIL:
         # Note: AuthMiddleware might populate mock email if local, 
         # but let's be explicit about the fallback here too.
         user_email = MOCK_USER_EMAIL

    # Query DB for this user
    logger.info(f"DEBUG: Querying DB for user email: '{user_email}' (repr: {repr(user_email)})")
    user = db.query(UserModel).filter(UserModel.email == user_email).first()
    
    if user:
         logger.info(f"DEBUG: Found user in DB. ID: {user.id}, Roles: {[r.name for r in user.roles]}")
    else:
         logger.info(f"DEBUG: User not found in DB for email: '{user_email}'")
    
    # Handling for New Users (Just-in-Time Provisioning)
    # If a valid Databricks user comes in but isn't in our DB, we should create them.
    if not user and user_email != MOCK_USER_EMAIL:
         logger.info(f"User {user_email} not found in DB. Auto-provisioning...")
         # Default role for new users is 'business_user'
         default_role = db.query(RoleModel).filter(RoleModel.name == "business_user").first()
         roles = [default_role] if default_role else []
         
         new_user = UserModel(
             id=str(uuid.uuid4()),
             email=user_email,
             # We might get name from headers if available
             full_name=request.state.user.get("username", user_email),
             is_active=True,
             roles=roles
         )
         db.add(new_user)
         db.commit()
         db.refresh(new_user)
         return new_user

    if not user:
         raise HTTPException(status_code=401, detail=f"User {user_email} not found in database. Root cause: possible sync or bootstrap error.")
            
    # DEV FEATURE: Role Override
    # STRICTLY for local development. This allows the UI to simulate other roles.
    if x_dev_role_override:
        # Check if we are allowed to use this (e.g. check environment)
        # For this MVP code, we assume if the header is present and valid role, we honor it
        # In production, this should be gated behind settings.ENVIRONMENT == "local"
        
        logger.info(f"DEV: Overriding role for user {user.email} to {x_dev_role_override}")
        
        # Determine target role
        target_role = None
        for r in user.roles:
            if r.name == x_dev_role_override:
                target_role = r
                break
        
        if not target_role:
             # If user doesn't have it, maybe fetch from DB (simulating "Login As")
             # Or just construct a fake role object if we want to simulate roles the user DOESN'T have

             target_role = db.query(RoleModel).filter(RoleModel.name == x_dev_role_override).first()
        
        if target_role:
            # Detach user from session to prevent this ephemeral change from affecting
            # the DB or other queries in this session (like GET /users list).
            db.expunge(user)
            db.expunge(target_role)
            
            # Create a clone ensuring we don't mutate DB session object permanently
            # But UserModel is an ORM object...
            # We just filter the roles list on the instance for this request scope
            # WARNING: Be careful not to commit this user back to DB!
            user.roles = [target_role]
            logger.info(f"DEV: Successfully overrode roles to: {[r.name for r in user.roles]} (User detached from session)")
        else:
             logger.warning(f"DEV override requested for '{x_dev_role_override}' but role not found")
    else:
        logger.debug(f"DEV: No role override header found. Current roles: {[r.name for r in user.roles]}")

    return user

def require_role(role_name: str):
    """
    Dependency factory to check if user has a specific role.
    Usage: @router.get("/", dependencies=[Depends(require_role("platform_admin"))])
    """
    def version_checker(user: UserModel = Depends(get_current_user)):
        if not user.has_role(role_name):
            logger.warning(f"Access denied for user {user.email}: requires role {role_name}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires role: {role_name}"
            )
        return user
    return version_checker

def require_any_role(role_names: list[str]):
    """
    Dependency factory to check if user has at least one of the specified roles.
    """
    def version_checker(user: UserModel = Depends(get_current_user)):
        if not any(user.has_role(role) for role in role_names):
            logger.warning(f"Access denied for user {user.email}: requires one of roles {role_names}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires one of these roles: {', '.join(role_names)}"
            )
        return user
    return version_checker

async def get_github_provider() -> GitHubProvider:
    """
    Dependency to get a GitHub provider instance.
    Uses GITHUB_TOKEN and GITHUB_ORG from settings.
    """
    async with GitHubProvider(
        token=settings.GITHUB_TOKEN,
        org=settings.GITHUB_ORG
    ) as github:
        yield github
