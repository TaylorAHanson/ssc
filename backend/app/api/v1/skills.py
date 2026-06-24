"""Agent Skills API — OBO CRUD over SKILL.md folders.

Every endpoint runs on-behalf-of the caller: the user's forwarded token
(``request.state.token``) is handed to the :class:`SkillsProvider`, which builds
a user-scoped WorkspaceClient. Personal skills live in the user's Workspace
folder; shared skills live in ``.skills`` dirs on any UC Volume the user can
read/write. Unity Catalog enforces who can see/edit what — we don't re-check.

Gated by the ``skills`` feature flag.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api import deps
from app.core.feature_flags import is_feature_enabled
from app.models.user import User
from app.providers.skills.client import (
    STORE_VOLUME,
    STORE_WORKSPACE,
    SkillsError,
    get_skills_provider,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _ensure_enabled() -> None:
    if not is_feature_enabled("skills"):
        raise HTTPException(status_code=404, detail="Skills feature is disabled.")


def _obo(request: Request) -> Optional[str]:
    return getattr(request.state, "token", None)


# --------------------------------------------------------------------- schemas

class SkillCreate(BaseModel):
    name: str = Field(..., min_length=1)
    content: str = ""
    description: str = ""
    store: str = STORE_WORKSPACE
    # For volume skills: the target ``.skills`` dir (from /skills/locations).
    base_path: Optional[str] = None


class SkillUpdate(BaseModel):
    name: str = Field(..., min_length=1)
    content: str = ""
    description: str = ""


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


@router.get("/locations")
async def list_locations(
    request: Request,
    include_shared: bool = True,
    user: User = Depends(deps.get_current_user),
):
    """Where the caller can create a new skill (personal + writable .skills)."""
    _ensure_enabled()
    provider = get_skills_provider()
    try:
        locations = provider.list_locations(_obo(request), user.email, include_shared=include_shared)
    except Exception as exc:  # noqa: BLE001
        logger.exception("list_locations failed")
        raise HTTPException(status_code=500, detail=f"Could not list locations: {exc}")
    return {"locations": [loc.to_dict() for loc in locations]}


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


@router.post("")
async def create_skill(
    body: SkillCreate,
    request: Request,
    user: User = Depends(deps.get_current_user),
):
    _ensure_enabled()
    if body.store not in (STORE_WORKSPACE, STORE_VOLUME):
        raise HTTPException(status_code=400, detail="Invalid store.")
    provider = get_skills_provider()
    try:
        skill = provider.save_skill(
            _obo(request),
            user.email,
            name=body.name,
            content=body.content,
            store=body.store,
            base_path=body.base_path,
            description=body.description,
        )
    except SkillsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("create_skill failed")
        raise HTTPException(status_code=500, detail=f"Could not create skill: {exc}")
    logger.info("Skill created by %s at %s", user.email, skill.dir_path)
    return skill.to_dict()


@router.put("/{skill_id}")
async def update_skill(
    skill_id: str,
    body: SkillUpdate,
    request: Request,
    user: User = Depends(deps.get_current_user),
):
    _ensure_enabled()
    provider = get_skills_provider()
    try:
        skill = provider.save_skill(
            _obo(request),
            user.email,
            name=body.name,
            content=body.content,
            description=body.description,
            skill_id=skill_id,
        )
    except SkillsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("update_skill failed")
        raise HTTPException(status_code=500, detail=f"Could not update skill: {exc}")
    logger.info("Skill updated by %s at %s", user.email, skill.dir_path)
    return skill.to_dict()


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: str,
    request: Request,
    user: User = Depends(deps.get_current_user),
):
    _ensure_enabled()
    provider = get_skills_provider()
    try:
        provider.delete_skill(_obo(request), skill_id)
    except SkillsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("delete_skill failed")
        raise HTTPException(status_code=500, detail=f"Could not delete skill: {exc}")
    logger.info("Skill deleted by %s (%s)", user.email, skill_id)
    return {"ok": True}
