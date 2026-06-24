"""Skill-authoring tools so the agent can co-create skills in chat.

A *skill* is a folder with a ``SKILL.md`` (YAML frontmatter ``name`` +
``description`` then markdown instructions). These tools let the agent list,
read, write and delete the caller's skills. They are **OBO** — every call uses
the user's forwarded token, so they only ever touch the caller's own Workspace
folder or the UC Volumes they can read/write. No special role is required
(unlike workflow authoring): any user can curate their own skills.

Gated by the ``skills`` feature flag. Mutating tools are ``app_write`` so they
route through the governed ToolExecutor (audited) without an approval gate.
"""
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool

_FEATURE = "skills"


def _provider():
    from app.providers.skills.client import get_skills_provider

    return get_skills_provider()


def _map_store(store: Optional[str]) -> str:
    from app.providers.skills.client import STORE_VOLUME, STORE_WORKSPACE

    s = (store or "personal").strip().lower()
    if s in ("personal", "workspace", "me"):
        return STORE_WORKSPACE
    if s in ("volume", "shared", "uc"):
        return STORE_VOLUME
    return STORE_WORKSPACE


# --------------------------------------------------------------------- schemas

class ListSkillsInput(BaseModel):
    include_shared: bool = Field(
        True,
        description="Also scan UC Volumes the user can access for shared `.skills` folders.",
    )


class GetSkillInput(BaseModel):
    skill_id: str = Field(..., description="The opaque skill id from list_skills.")


class SaveSkillInput(BaseModel):
    name: str = Field(..., description="Human-readable skill name.")
    content: str = Field(
        "",
        description=(
            "The skill body in markdown. May be a bare body (frontmatter is added "
            "automatically from name+description) OR a full SKILL.md that already "
            "starts with a `---` frontmatter block."
        ),
    )
    description: str = Field(
        "",
        description="One-line description of WHEN the agent should use this skill.",
    )
    store: str = Field(
        "personal",
        description="Where to save: 'personal' (your Workspace folder) or 'volume' (a shared .skills dir).",
    )
    target_path: Optional[str] = Field(
        None,
        description=(
            "For store='volume': the target `.skills` directory (use list_skill_locations). "
            "Ignored for personal skills."
        ),
    )
    skill_id: Optional[str] = Field(
        None,
        description="To UPDATE an existing skill in place, pass its id. Omit to create a new one.",
    )


class DeleteSkillInput(BaseModel):
    skill_id: str = Field(..., description="The opaque skill id to delete.")


# ----------------------------------------------------------------------- tools

@tool(
    name="list_skills",
    description=(
        "List the skills available to the user: personal skills (their Workspace "
        "`.skills` folder) and shared skills (in `.skills` folders on UC Volumes "
        "they can access). Returns id, name, description, and storage location. "
        "Call this before editing so you reference a real skill id."
    ),
    args_schema=ListSkillsInput,
    feature_flag=_FEATURE,
    friendly_label="Loading your skills...",
)
async def list_skills(include_shared: bool = True, **kwargs: Any) -> Dict[str, Any]:
    obo = kwargs.get("_obo_token")
    email = kwargs.get("_user_email") or ""
    skills = _provider().list_skills(obo, email, include_shared=include_shared)
    return {"skills": [s.to_dict() for s in skills], "count": len(skills)}


@tool(
    name="list_skill_locations",
    description=(
        "List the places the user can save a NEW skill: their personal Workspace "
        "folder plus any `.skills` directory on UC Volumes they can write to. Use "
        "the returned base_path as save_skill's target_path for shared skills."
    ),
    feature_flag=_FEATURE,
    friendly_label="Finding where you can save skills...",
)
async def list_skill_locations(**kwargs: Any) -> Dict[str, Any]:
    obo = kwargs.get("_obo_token")
    email = kwargs.get("_user_email") or ""
    locations = _provider().list_locations(obo, email, include_shared=True)
    return {"locations": [loc.to_dict() for loc in locations]}


@tool(
    name="get_skill",
    description="Read a single skill's full SKILL.md content (and metadata) by id.",
    args_schema=GetSkillInput,
    feature_flag=_FEATURE,
    friendly_label="Reading skill...",
)
async def get_skill(skill_id: str, **kwargs: Any) -> Dict[str, Any]:
    obo = kwargs.get("_obo_token")
    skill = _provider().get_skill(obo, skill_id)
    return skill.to_dict()


@tool(
    name="save_skill",
    description=(
        "Create or update a skill (a SKILL.md folder). Pass skill_id to update an "
        "existing one in place, or omit it to create a new one. For a personal "
        "skill leave store='personal'; for a shared skill set store='volume' and "
        "target_path to a `.skills` dir from list_skill_locations. Write a clear "
        "name, a one-line description of WHEN to use the skill, and step-by-step "
        "instructions in content. Only call after the user has confirmed the draft."
    ),
    args_schema=SaveSkillInput,
    side_effect_class="app_write",
    feature_flag=_FEATURE,
    friendly_label="Saving skill...",
    friendly_completion_label="Skill saved",
)
async def save_skill(
    name: str,
    content: str = "",
    description: str = "",
    store: str = "personal",
    target_path: Optional[str] = None,
    skill_id: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    from app.providers.skills.client import SkillsError

    obo = kwargs.get("_obo_token")
    email = kwargs.get("_user_email") or ""
    try:
        skill = _provider().save_skill(
            obo,
            email,
            name=name,
            content=content,
            description=description,
            store=_map_store(store),
            base_path=target_path,
            skill_id=skill_id,
        )
    except SkillsError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "skill": skill.to_dict()}


@tool(
    name="delete_skill",
    description="Delete a skill folder by id. Confirm with the user before calling.",
    args_schema=DeleteSkillInput,
    side_effect_class="app_write",
    feature_flag=_FEATURE,
    friendly_label="Deleting skill...",
    friendly_completion_label="Skill deleted",
)
async def delete_skill(skill_id: str, **kwargs: Any) -> Dict[str, Any]:
    from app.providers.skills.client import SkillsError

    obo = kwargs.get("_obo_token")
    try:
        _provider().delete_skill(obo, skill_id)
    except SkillsError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}
