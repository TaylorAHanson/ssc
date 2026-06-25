"""Agent Skills API — read-only OBO access over SKILL.md folders.

Every endpoint runs on-behalf-of the caller: the user's forwarded token
(``request.state.token``) is handed to the :class:`SkillsProvider`, which builds
a user-scoped WorkspaceClient. Personal skills live in the user's Workspace
folder; shared skills live in ``.skills`` dirs on any UC Volume the user can
read. Unity Catalog enforces who can see what — we don't re-check.

Skill *authoring* (create/update/delete) now lives in the Command Center's
Agent Studio; this app only loads skills at run time. Gated by the ``skills``
feature flag.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api import deps
from app.core.feature_flags import is_feature_enabled
from app.models.user import User
from app.providers.skills.client import SkillsError, get_skills_provider

logger = logging.getLogger(__name__)

router = APIRouter()


def _ensure_enabled() -> None:
    if not is_feature_enabled("skills"):
        raise HTTPException(status_code=404, detail="Skills feature is disabled.")


def _obo(request: Request) -> Optional[str]:
    return getattr(request.state, "token", None)


# --------------------------------------------------------------------- routes

@router.get("")
async def list_skills(
    request: Request,
    include_shared: bool = True,
    user: User = Depends(deps.get_current_user),
):
    """List the caller's skills (personal + discovered shared)."""
    _ensure_enabled()
    provider = get_skills_provider()
    try:
        skills = provider.list_skills(_obo(request), user.email, include_shared=include_shared)
    except SkillsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("list_skills failed")
        raise HTTPException(status_code=500, detail=f"Could not list skills: {exc}")
    return {"skills": [s.to_dict() for s in skills]}


@router.get("/{skill_id}")
async def get_skill(
    skill_id: str,
    request: Request,
    user: User = Depends(deps.get_current_user),
):
    _ensure_enabled()
    provider = get_skills_provider()
    try:
        skill = provider.get_skill(_obo(request), skill_id)
    except SkillsError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_skill failed")
        raise HTTPException(status_code=500, detail=f"Could not read skill: {exc}")
    return skill.to_dict()
