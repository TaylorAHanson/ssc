"""Default identity provider: records the intent, performs no external call.

Lets the app run out-of-the-box (and in dev/tests) without a configured identity
system. Membership changes are logged and returned as succeeded so workflows
complete; an admin swaps in ``rest``/``lmws`` for real enforcement.
"""
import logging
from typing import Any, Dict, List, Optional

from app.providers.identity.base import IdentityGroupProvider

logger = logging.getLogger(__name__)


class NoopIdentityProvider(IdentityGroupProvider):
    async def list_members_add(self, group, members, justification=None) -> Dict[str, Any]:
        logger.info("[identity:noop] add %s -> %s", members, group)
        return {"group": group, "added": members, "applied": False, "provider": "noop"}

    async def list_members_remove(self, group, members, justification=None) -> Dict[str, Any]:
        logger.info("[identity:noop] remove %s from %s", members, group)
        return {"group": group, "removed": members, "applied": False, "provider": "noop"}

    async def list_members_retrieve(self, group: str) -> Dict[str, Any]:
        return {"group": group, "members": [], "provider": "noop"}

    async def member_retrieve(self, member: str) -> Dict[str, Any]:
        return {"member": member, "groups": [], "provider": "noop"}
