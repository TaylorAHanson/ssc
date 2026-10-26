"""
Context Catalog Git integration API: sync status, "Sync from Git", and the
direct-editing guard used by the catalog's write endpoints.
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.core.feature_flags import is_feature_enabled
from app.models.user import User
from app.services import context_catalog_git as git_sync

logger = logging.getLogger(__name__)

router = APIRouter()

_WRITE_ROLES = ["Platform Admin", "Governance Admin"]


def _require_feature() -> None:
    if not is_feature_enabled("context_catalog"):
        raise HTTPException(status_code=404, detail="Context Catalog is not enabled")


def require_direct_editing() -> None:
    """Reject catalog edits made in the app when the environment is managed from Git."""
    if not git_sync.direct_editing_enabled():
        raise HTTPException(status_code=403, detail=git_sync.direct_editing_message())


@router.get("/status")
def get_git_status(
    db: Session = Depends(deps.get_db),
    _user: User = Depends(deps.get_current_user),
    _: None = Depends(_require_feature),
):
    return git_sync.get_status(db)


@router.post("/sync")
async def sync_from_git(
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
):
    """Pull the branch into the catalog now (and push any queued saves)."""
    cfg = git_sync.resolve_config()
    if not cfg.active:
        raise HTTPException(status_code=400, detail=cfg.problem)
    result = await asyncio.to_thread(git_sync.sync_once, "manual", current_user.email)
    if result.get("busy"):
        raise HTTPException(status_code=409, detail=result["error"])
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "Sync failed")
    return result
