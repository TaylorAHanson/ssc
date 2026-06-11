"""Identity-group provider interface (vendor-neutral)."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class IdentityGroupProvider(ABC):
    """Manage membership of identity groups (AD/Entra/Okta/SCIM/LMWS-backed).

    The access/approver group *names* are resolved from configurable UC tag keys
    (``settings.ACCESS_GROUP_TAG_KEY`` / ``APPROVER_GROUP_TAG_KEY``); this
    interface just adds/removes/reads members for a given group name.
    """

    @abstractmethod
    async def list_members_add(self, group: str, members: List[str],
                               justification: Optional[str] = None) -> Dict[str, Any]:
        ...

    @abstractmethod
    async def list_members_remove(self, group: str, members: List[str],
                                  justification: Optional[str] = None) -> Dict[str, Any]:
        ...

    @abstractmethod
    async def list_members_retrieve(self, group: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    async def member_retrieve(self, member: str) -> Dict[str, Any]:
        ...

    async def health_check(self) -> bool:
        return True
