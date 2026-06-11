"""Legacy LMWS adapter behind the vendor-neutral IdentityGroupProvider.

Kept as one selectable backend (``IDENTITY_PROVIDER=lmws``) for the Qualcomm
FWS-API. Most deployments use ``noop`` or ``rest`` instead.
"""
from typing import Any, Dict, List, Optional

from app.providers.identity.base import IdentityGroupProvider


class LmwsIdentityProvider(IdentityGroupProvider):
    def __init__(self):
        from app.providers.lmws import LmwsProvider
        self._lmws = LmwsProvider()

    async def list_members_add(self, group, members, justification=None) -> Dict[str, Any]:
        return await self._lmws.add_members(group, members, justification=justification)

    async def list_members_remove(self, group, members, justification=None) -> Dict[str, Any]:
        return await self._lmws.remove_members(group, members, justification=justification)

    async def list_members_retrieve(self, group: str) -> Dict[str, Any]:
        return await self._lmws.list_retrieve(group)

    async def member_retrieve(self, member: str) -> Dict[str, Any]:
        return await self._lmws.member_retrieve(member)
