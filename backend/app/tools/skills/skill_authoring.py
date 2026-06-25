"""Read-only skill tools so the agent can load skills at run time.

A *skill* is a folder with a ``SKILL.md`` (YAML frontmatter ``name`` +
``description`` then markdown instructions). These tools let the agent list and
read the caller's skills. They are **OBO** — every call uses the user's
forwarded token, so they only ever surface the caller's own Workspace folder or
the UC Volumes they can read.

Skill *authoring* (create/update/delete) now lives in the Command Center's
Agent Studio, which writes skills as files to UC Volumes; this app only loads
them at run time. Gated by the ``skills`` feature flag.
"""
from typing import Any, Dict

from pydantic import BaseModel, Field

from app.tools.mcp import tool

_FEATURE = "skills"


def _provider():
    from app.providers.skills.client import get_skills_provider

    return get_skills_provider()


# --------------------------------------------------------------------- schemas

class ListSkillsInput(BaseModel):
    include_shared: bool = Field(
        True,
        description="Also scan UC Volumes the user can access for shared `.skills` folders.",
    )


class GetSkillInput(BaseModel):
    skill_id: str = Field(..., description="The opaque skill id from list_skills.")


# ----------------------------------------------------------------------- tools

@tool(
    name="list_skills",
    description=(
        "List the skills available to the user: personal skills (their Workspace "
        "`.skills` folder) and shared skills (in `.skills` folders on UC Volumes "
        "they can access). Returns id, name, description, and storage location. "
        "Call this to discover a skill before loading it with get_skill."
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
