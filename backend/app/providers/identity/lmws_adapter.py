"""LMWS adapter behind the vendor-neutral IdentityGroupProvider.

One selectable backend (``IDENTITY_PROVIDER=lmws``) for the Qualcomm FWS-API.
Each operation runs against the LMWS/FWS-API gateway either **natively**
(in-process HTTP via :class:`LmwsNativeClient`, the default) or via the
**serverless** notebook job (:class:`LmwsProvider`), selected at call time by
``settings.LMWS_NATIVE`` so the path can be flipped from Admin -> Settings
without a redeploy.
"""
from typing import Any, Dict

from app.core.config import settings
from app.providers.identity.base import IdentityGroupProvider


class LmwsIdentityProvider(IdentityGroupProvider):
    def __init__(self):
        from app.providers.lmws import LmwsProvider

        self._lmws = LmwsProvider()

    @staticmethod
    def _use_native() -> bool:
        """Native (in-app) vs serverless (job) path — read per call so the toggle is live."""
        return bool(getattr(settings, "LMWS_NATIVE", True))

    @staticmethod
    def _native():
        from app.providers.lmws import LmwsNativeClient

        return LmwsNativeClient()

    async def list_members_add(self, group, members, justification=None) -> Dict[str, Any]:
        if self._use_native():
            return await self._native().add_members(group, members, justification=justification)
        return await self._lmws.add_members(group, members, justification=justification)

    async def list_members_remove(self, group, members, justification=None) -> Dict[str, Any]:
        if self._use_native():
            return await self._native().remove_members(group, members, justification=justification)
        return await self._lmws.remove_members(group, members, justification=justification)

    async def list_members_retrieve(self, group: str) -> Dict[str, Any]:
        if self._use_native():
            return await self._native().list_retrieve(group)
        return await self._lmws.list_retrieve(group)

    async def member_retrieve(self, member: str) -> Dict[str, Any]:
        if self._use_native():
            return await self._native().member_retrieve(member)
        return await self._lmws.member_retrieve(member)
