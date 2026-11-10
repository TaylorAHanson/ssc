"""
The Workspaces page: the Databricks workspaces the caller can use.

Read from the Access Rules for the caller's own groups, so it always describes
the signed-in user and never anyone else.
"""
import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import deps
from app.core.feature_flags import is_feature_enabled
from app.core.links import genie_one_url
from app.models.user import User
from app.services import my_workspaces

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/mine")
def list_my_workspaces(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """The caller's workspaces, each with links to open it and its Genie One.

    ``access_rules_enabled`` is false when the Access Rules feature is off, and
    ``groups_known`` is false when the caller's groups weren't available, so the
    page can say why the list is empty instead of showing a bare "none".
    """
    base = {"genie_one_url": genie_one_url()}
    if not is_feature_enabled("rbac"):
        return {**base, "workspaces": [], "access_rules_enabled": False, "groups_known": True}
    me = (current_user.email or "").strip().lower()
    groups_known = any(str(e or "").strip().lower() not in ("", me) for e in current_user.entitlements or [])
    groups = my_workspaces.user_groups(current_user.entitlements, current_user.email)
    workspaces = my_workspaces.workspaces_for(db, groups)
    logger.info(
        "Listed %d workspace(s) for %s from %d group(s)",
        len(workspaces), current_user.email, len(groups),
    )
    return {
        **base,
        "workspaces": workspaces,
        "access_rules_enabled": True,
        "groups_known": groups_known,
    }
