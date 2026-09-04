"""
Agent tool for reading the caller's own context (the "user model").

The agent normally receives this in its system prompt already, rendered and
truncated. This tool exists for the cases the prompt block can't cover: pulling a
list that was cut short (someone in 200 groups), asking for one section on its
own, or forcing a rebuild when the user says their access just changed.
"""
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool

logger = logging.getLogger(__name__)


class GetUserContextInput(BaseModel):
    sections: Optional[List[str]] = Field(
        default=None,
        description=(
            "Which sections to return: 'identity' (name, roles, persona), "
            "'activity' (their recent requests, approvals waiting on them, recent "
            "topics), 'groups' (identity-provider group memberships). Omit for all."
        ),
    )
    refresh: bool = Field(
        default=False,
        description=(
            "Rebuild from source before returning instead of using the cache. Slow "
            "(the group lookup can take 30s), so only use it when the user says "
            "something just changed and the cached answer looks wrong."
        ),
    )


@tool(
    name="get_user_context",
    args_schema=GetUserContextInput,
    side_effect_class="read",
    feature_flag="user_context",
    friendly_label="Checking who you are",
    friendly_completion_label="Reviewed your profile",
    description=(
        "Retrieve the current user's profile: name, roles, recent request submissions, "
        "pending approvals, and identity group memberships. Scoped strictly to the caller."
    ),
)
async def get_user_context_tool(
    sections: Optional[List[str]] = None,
    refresh: bool = False,
    _user_email: Optional[str] = None,
    _user_roles: Optional[str] = None,
    _user_entitlements: Optional[str] = None,
) -> Dict[str, Any]:
    """Read (or rebuild) the calling user's cached context."""
    from app.db.session import get_lakebase_session
    from app.services.approval_scope import parse_csv
    from app.services.user_context import (
        UserIdentity,
        enabled_sections,
        get_user_context,
    )

    if not _user_email:
        return {
            "error": (
                "No user identity was available on this call, so there is no profile "
                "to read. Ask the user directly instead."
            )
        }

    # Build the identity from the injected kwargs rather than re-querying the
    # IdP: the ToolExecutor already resolved it for this turn.
    identity = UserIdentity(
        email=_user_email,
        roles=parse_csv(_user_roles),
        entitlements=parse_csv(_user_entitlements),
    )

    requested = [s for s in (sections or []) if s]
    unknown = [s for s in requested if s not in enabled_sections()]
    wanted = [s for s in requested if s in enabled_sections()] or None

    db = get_lakebase_session()
    try:
        payload = await get_user_context(db, identity, sections=wanted, force=refresh)
    finally:
        db.close()

    result: Dict[str, Any] = dict(payload)
    if wanted:
        result["sections"] = {
            name: body for name, body in (payload.get("sections") or {}).items() if name in wanted
        }
    if unknown:
        result["unavailable_sections"] = unknown
    return result
