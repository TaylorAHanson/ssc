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
    member: str = Field(..., description="The user (CN or email) whose group memberships to look up.")


@tool(
    name="member_lookup",
    description=(
        "Look up all identity group/list memberships for a given user. Use to "
        "verify a user exists and see which groups they already belong to."
    ),
    args_schema=MemberLookupInput,
    side_effect_class="read",
)
async def member_lookup(member: str) -> Dict[str, Any]:
    logger.info("member_lookup: %s", member)
    try:
        return await get_identity_provider().member_retrieve(member)
    except RetryableError:
        raise
    except Exception as e:
        raise RetryableError(f"Failed to retrieve memberships for '{member}': {e}")
