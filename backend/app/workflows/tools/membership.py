"""
Identity membership and notification workflow tools.
"""
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool
from app.workflows.tools import _common

logger = logging.getLogger(__name__)


class GroupMembershipInput(BaseModel):
    group: str = Field(..., description="Identity group / list name")
    members: List[str] = Field(..., description="Members to add")


@tool(
    name="add_group_membership",
    args_schema=GroupMembershipInput,
    side_effect_class="membership",
    description=(
        "Add members to an identity group/list (Entra/Okta/SCIM/LMWS-backed). "
        "To verify before adding, look up the USER with member_lookup — do NOT "
        "call group_lookup first: restricted / N2K lists reject that lookup even "
        "when the add is valid, so gating on it wrongly blocks the request. The "
        "membership backend authorizes the write itself; if the caller isn't "
        "entitled, this tool surfaces that error."
    ),
)
async def add_group_membership(group: str, members: List[str], **kwargs) -> Dict[str, Any]:
    from app.tools.self_service.identity_groups import _normalize_member

    normalized = [_normalize_member(m) for m in members]
    normalized = [m for m in normalized if m]
    if normalized != members:
        logger.info("add_group_membership: normalized members %r -> %r", members, normalized)
    provider = _common._get_identity_provider()
    result = await provider.list_members_add(group, normalized)
    return {"group": group, "members": normalized, "result": result}


class NotifyInput(BaseModel):
    subject: str = Field(...)
    body: str = Field(...)
    to_email: Optional[str] = Field(default=None)


@tool(
    name="send_notification",
    args_schema=NotifyInput,
    side_effect_class="notify",
    description="Send an email/Teams notification.",
)
async def send_notification(
    subject: str,
    body: str,
    to_email: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    from app.core.config import settings

    provider = _common._get_notification_provider()
    recipient = to_email or settings.GOVERNANCE_EMAIL_GROUP
    recipients = [e.strip() for e in str(recipient or "").split(",") if e.strip()]
    results = [
        await provider.send_email(to=r, subject=subject, body=body, is_html=True)
        for r in recipients
    ]
    return {"sent": any(results), "to": recipients, "result": results}
