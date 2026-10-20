"""
API dependencies.
"""
from fastapi import Depends, HTTPException, status, Header, Request
from typing import Generator, Optional, List, Dict, Tuple
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.core.config import settings, dev_features_allowed as _dev_features_allowed
import uuid
import logging
import time
from app.models.user import User
from app.db.role_mapping import RoleMappingModel
from app.providers.github.client import GitHubProvider
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import DatabricksError

logger = logging.getLogger(__name__)

MOCK_USER_EMAIL = "admin@example.com"

ACCOUNT_GROUPS_ME_PATH = "/api/2.0/account/scim/v2/Me"
ACCOUNT_GROUP_CACHE_SECONDS = 300.0
# User email -> (fetched at, account group names). A failed read caches an empty list
# so a refused call doesn't slow every request.
_account_group_cache: Dict[str, Tuple[float, List[str]]] = {}


def _account_groups(w: WorkspaceClient, user_email: str) -> List[str]:
    """The user's account-level groups, read with their own token.

    The workspace's ``me()`` leaves out account groups that aren't assigned to the
    workspace, but Unity Catalog grants and approver groups can still name them.
    """
    now = time.monotonic()
    cached = _account_group_cache.get(user_email)
    if cached and now - cached[0] < ACCOUNT_GROUP_CACHE_SECONDS:
        return cached[1]
    try:
        me = w.api_client.do("GET", ACCOUNT_GROUPS_ME_PATH, query={"attributes": "groups"}) or {}
        groups = [g["display"] for g in me.get("groups") or [] if g.get("display")]
    except Exception as e:  # noqa: BLE001 - any refusal or failure falls back to workspace groups
        logger.warning("Couldn't read account groups for %s; using workspace groups only: %s", user_email, e)
        groups = []
    _account_group_cache[user_email] = (time.monotonic(), groups)
    return groups


def _get_user_entitlements(user_email: str, obo_token: Optional[str] = None) -> List[str]:
    """Fetch SCIM entitlements (groups/roles) using Databricks SDK."""
    entitlements = [user_email]
    
    # Local dev mock fallback: return 'users' per instructions
    if user_email == MOCK_USER_EMAIL and _dev_features_allowed():
        entitlements.append("users")
        return entitlements

    try:
        if obo_token and settings.DATABRICKS_HOST:
            # Use OBO token to fetch actual user's groups
            # Explicitly force auth_type="pat" to avoid "more than one authorization method" error 
            # when running inside a Databricks App (which injects Service Principal OAuth env vars)
            w = WorkspaceClient(
                host=settings.DATABRICKS_HOST, 
                token=obo_token,
                auth_type="pat"
            )
        else:
            # Fallback to default auth (which in Apps is the Service Principal)
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

        # Only with the user's own token: the fallback client is the app's identity.
        if obo_token:
            entitlements.extend(g for g in _account_groups(w, user_email) if g not in entitlements)
                    
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
        
    # The auth middleware substitutes ``settings.MOCK_USER_EMAIL`` when the proxy
    # sent no ``x-forwarded-email``, so both branches here mean the same thing:
    # this request carries no real forwarded identity.
    if not user_email or user_email == settings.MOCK_USER_EMAIL:
        if not _dev_features_allowed():
            # Fail closed. Collapsing an unidentified caller onto the shared
            # ``admin@example.com`` identity would hand them whatever
            # ``role_mappings`` grants that address (Platform Admin, as seeded)
            # *and* pool their chat transcripts and cached user context with
            # every other unidentified caller.
            logger.warning(
                "Rejecting request with no forwarded identity: ENVIRONMENT=%r is "
                "not dev-flavored, so the mock-user fallback is disabled.",
                settings.ENVIRONMENT,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthenticated: no user identity was forwarded with this request.",
            )
        user_email = MOCK_USER_EMAIL
        user_name = "System Admin"


    obo_token = getattr(request.state, "token", None)
    entitlements = _get_user_entitlements(user_email, obo_token)
    calculated_roles = _calculate_roles(db, entitlements)
    
    # Local dev mock fallback: if 'users' in entitlements and local dev, ensure 'Platform Admin' or 'User' is present.
    if _dev_features_allowed() and "users" in entitlements and not calculated_roles:
        # Fallback to giving Platform Admin to the mock user if no DB seeding occurred yet
        calculated_roles = ["Platform Admin"]
    
    # DEV FEATURE: Role Override (local/dev-flavored envs only; never stage/prod)
    if x_dev_role_override and _dev_features_allowed():
        logger.debug(f"DEV: Overriding role for user {user_email} to {x_dev_role_override}")
        calculated_roles = [x_dev_role_override]
    elif x_dev_role_override:
        logger.warning(
            "Ignoring X-Dev-Role-Override=%r for %s: ENVIRONMENT=%r is not a "
            "dev-flavored environment.",
            x_dev_role_override, user_email, settings.ENVIRONMENT,
        )
            
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
        token=settings.GITHUB_TOKEN or settings.get_git_token(),
        org=settings.GITHUB_ORG
    ) as github:
        yield github
