"""
Identity-group lookup tools for the agent (vendor-neutral).

Backed by the pluggable ``IdentityGroupProvider`` (``settings.IDENTITY_PROVIDER``),
so the same tools work against SCIM/Entra/Okta/LMWS or the default no-op backend.
Reads only; membership *writes* go through the governed ``add_group_membership``
V2 tool under the approval gate.
"""
import logging
from typing import Any, Dict

from pydantic import BaseModel, Field

from app.core.exceptions import RetryableError
from app.providers.identity import get_identity_provider
from app.tools.mcp import tool

logger = logging.getLogger(__name__)


class GroupLookupInput(BaseModel):
    group: str = Field(..., description="Exact name of the identity group/list to look up.")


@tool(
    name="group_lookup",
    description=(
        "Look up an identity group/list by its exact name, returning its members "
        "(and owner/supervisors where the backend provides them). Use to verify a "
        "group exists or inspect its membership before requesting changes."
    ),
    args_schema=GroupLookupInput,
    side_effect_class="read",
)
async def group_lookup(group: str) -> Dict[str, Any]:
    logger.info("group_lookup: %s", group)
    try:
        return await get_identity_provider().list_members_retrieve(group)
    except RetryableError:
        raise
    except Exception as e:
        raise RetryableError(f"Failed to retrieve group '{group}': {e}")


class MemberLookupInput(BaseModel):
    member: str = Field(
        ...,
        description=(
            "The user whose group memberships to look up. Accepts either a corporate "
            "username (CN) or an email address — if an email is given, the part before "
            "'@' is used automatically (e.g. 'taylhans@qualcomm.com' -> 'taylhans')."
        ),
    )


def _normalize_member(member: str) -> str:
    """Reduce a member identifier to the corporate username the directory keys on.

    The directory looks users up by their corporate username (the local part of
    the email), not the full address, so a raw ``user@domain`` misses. Default to
    the part before ``@`` so callers can pass either form interchangeably.
    """
    normalized = (member or "").strip()
    if "@" in normalized:
        normalized = normalized.split("@", 1)[0].strip()
    return normalized


@tool(
    name="member_lookup",
    description=(
        "Look up all identity group/list memberships for a given user. Use to "
        "verify a user exists and see which groups they already belong to. Accepts "
        "a corporate username or an email address (the part before '@' is used)."
    ),
    args_schema=MemberLookupInput,
    side_effect_class="read",
)
async def member_lookup(member: str) -> Dict[str, Any]:
    normalized = _normalize_member(member)
    if normalized != member:
        logger.info("member_lookup: %s (normalized from %r)", normalized, member)
    else:
        logger.info("member_lookup: %s", normalized)
    if not normalized:
        raise RetryableError("member_lookup requires a non-empty username or email.")
    try:
        return await get_identity_provider().member_retrieve(normalized)
    except RetryableError:
        raise
    except Exception as e:
        raise RetryableError(f"Failed to retrieve memberships for '{normalized}': {e}")
