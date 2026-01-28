"""
API dependencies.
"""
from fastapi import Depends, HTTPException, status, Header
from typing import Generator, Optional
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.user import UserModel
import logging

logger = logging.getLogger(__name__)

# Mock user for development - in production this would verify JWT/OAuth
MOCK_USER_EMAIL = "admin@qualcomm.com"

def get_current_user(
    db: Session = Depends(get_db),
    x_dev_role_override: Optional[str] = Header(None, alias="X-Dev-Role-Override")
) -> UserModel:
    """
    Get the current authenticated user.
    For MVP, returns the mock admin user or first user in DB.
    """
    # Try to find the mock user
    user = db.query(UserModel).filter(UserModel.email == MOCK_USER_EMAIL).first()
    
    if not user:
        # Bootstrap default admin user if missing (fix for "chicken and egg" problem)
        logger.info(f"User {MOCK_USER_EMAIL} not found. Bootstrapping default admin user...")
        
        try:
            # 1. Ensure Roles exist
            from app.db.user import RoleModel
            import uuid
            
            roles = [
                {"id": "role_platform_admin", "name": "platform_admin", "description": "Full system access"},
                {"id": "role_governance_admin", "name": "governance_admin", "description": "Governance and policy management"},
                {"id": "role_security_admin", "name": "security_admin", "description": "Security auditing and access control"},
                {"id": "role_finance_admin", "name": "finance_admin", "description": "Budget and cost management"},
                {"id": "role_business_user", "name": "business_user", "description": "Standard business user access"},
            ]
            
            for role_data in roles:
                if not db.query(RoleModel).filter(RoleModel.name == role_data["name"]).first():
                    db.add(RoleModel(**role_data))
            db.commit()

            # 2. Create Admin User
            user = UserModel(
                id=str(uuid.uuid4()),
                email=MOCK_USER_EMAIL,
                full_name="System Admin",
                is_active=True
            )
            
            # Assign all roles
            user.roles = db.query(RoleModel).all()
            
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"Successfully bootstrapped user: {user.email}")
            
        except Exception as e:
            logger.error(f"Failed to bootstrap admin user: {e}")
            # If bootstrap fails, fall back to raising 401
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Could not validate credentials and bootstrap failed: {str(e)}"
            )
            
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
             from app.db.user import RoleModel
             target_role = db.query(RoleModel).filter(RoleModel.name == x_dev_role_override).first()
        
        if target_role:
            # Create a clone ensuring we don't mutate DB session object permanently
            # But UserModel is an ORM object...
             # We just filter the roles list on the instance for this request scope
             # WARNING: Be careful not to commit this user back to DB!
             user.roles = [target_role]
        else:
             logger.warning(f"DEV override requested for '{x_dev_role_override}' but role not found")

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
