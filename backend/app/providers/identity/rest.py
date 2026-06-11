"""Generic SCIM/REST identity provider (config-driven).

Targets any group API that speaks SCIM 2.0-ish or a simple REST contract. The
base URL, auth token, and path templates come from settings so a customer points
it at Entra (Graph), Okta, or a homegrown SCIM endpoint without code changes:

    IDENTITY_PROVIDER=rest
    IDENTITY_REST_BASE_URL=https://graph.microsoft.com/v1.0
    IDENTITY_REST_TOKEN=<bearer>
    IDENTITY_REST_ADD_PATH=/groups/{group}/members/$ref
    IDENTITY_REST_REMOVE_PATH=/groups/{group}/members/{member}/$ref
    IDENTITY_REST_GROUP_PATH=/groups/{group}/members
    IDENTITY_REST_MEMBER_PATH=/users/{member}/memberOf
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
from app.providers.identity.base import IdentityGroupProvider

logger = logging.getLogger(__name__)


class RestIdentityProvider(IdentityGroupProvider):
    def __init__(self):
        self.base_url = (settings.IDENTITY_REST_BASE_URL or "").rstrip("/")
        self.token = settings.IDENTITY_REST_TOKEN or ""
        if not self.base_url:
            raise PermanentError("IDENTITY_REST_BASE_URL is required for IDENTITY_PROVIDER=rest")

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def _request(self, method: str, path: str, **kw) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(method, url, headers=self._headers(), **kw)
                resp.raise_for_status()
                return resp.json() if resp.content else {}
        except httpx.HTTPStatusError as e:
            raise PermanentError(f"Identity REST {method} {path} failed: {e.response.text}")
        except httpx.RequestError as e:
            raise RetryableError(f"Identity REST {method} {path} unreachable: {e}")

    async def list_members_add(self, group, members, justification=None) -> Dict[str, Any]:
        path_tpl = settings.IDENTITY_REST_ADD_PATH or "/groups/{group}/members"
        results = [await self._request("POST", path_tpl.format(group=group, member=m),
                                       json={"member": m, "justification": justification})
                   for m in members]
        return {"group": group, "added": members, "results": results, "provider": "rest"}

    async def list_members_remove(self, group, members, justification=None) -> Dict[str, Any]:
        path_tpl = settings.IDENTITY_REST_REMOVE_PATH or "/groups/{group}/members/{member}"
        results = [await self._request("DELETE", path_tpl.format(group=group, member=m))
                   for m in members]
        return {"group": group, "removed": members, "results": results, "provider": "rest"}

    async def list_members_retrieve(self, group: str) -> Dict[str, Any]:
        path = (settings.IDENTITY_REST_GROUP_PATH or "/groups/{group}/members").format(group=group)
        return {"group": group, "members": await self._request("GET", path), "provider": "rest"}

    async def member_retrieve(self, member: str) -> Dict[str, Any]:
        path = (settings.IDENTITY_REST_MEMBER_PATH or "/users/{member}/memberOf").format(member=member)
        return {"member": member, "groups": await self._request("GET", path), "provider": "rest"}
