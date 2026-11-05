"""Confluence for the catalog reviewer, through Databricks' external MCP proxy.

The Atlassian MCP server is registered as a Unity Catalog HTTP connection and
reached at ``/api/2.0/mcp/external/<connection>`` with the token of the admin
who selected Review. On a per-user OAuth connection the proxy uses that admin's
own Atlassian sign-in, so the reviewer sees only the pages they can see.

Only tools named in ``READ_ONLY_TOOLS`` are offered to the model, whatever the
server lists, and a call to any other name is refused here. The list covers the
Rovo MCP v2 names and the v1 names, since the connection decides which server
version is behind it.
"""
from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)

TOOL_PREFIX = "confluence__"

READ_ONLY_TOOLS = frozenset({
    # Rovo MCP v2. executeRead runs a deferred tool from discover, and only
    # ones Atlassian classes as reads (writes need executeWrite).
    "atlassianUserInfo",
    "getAccessibleAtlassianResources",
    "searchConfluence",
    "getConfluenceContent",
    "listConfluenceContent",
    "listConfluenceSpaces",
    "getConfluenceSpace",
    "listConfluenceComments",
    "listConfluenceContentVersions",
    "discover",
    "executeRead",
    "search",
    "fetch",
    # Rovo MCP v1.
    "searchConfluenceUsingCql",
    "getConfluencePage",
    "getConfluenceSpaces",
    "getPagesInConfluenceSpace",
    "getConfluencePageDescendants",
    "getConfluencePageFooterComments",
    "getConfluencePageInlineComments",
    "lookupJiraAccountId",
})

_OUTPUT_LIMIT = 15_000
_DESCRIPTION_LIMIT = 1_000
_CALL_TIMEOUT_SECONDS = 60
_MAX_TOOL_PAGES = 10


@dataclass
class ConfluenceAccess:
    """What the reviewer can use: ``status`` is available, not_configured or unavailable."""

    status: str
    note: str = ""
    session: Optional["ConfluenceSession"] = None


class ConfluenceSession:
    def __init__(self, session: Any, tools: List[Any], *, cloud_id: Optional[str] = None,
                 site_url: Optional[str] = None):
        self._session = session
        self._tools = {t.name: t for t in tools if getattr(t, "name", None) in READ_ONLY_TOOLS}
        self.cloud_id = cloud_id
        self.site_url = site_url
        self.calls = 0

    @property
    def tool_names(self) -> List[str]:
        return sorted(self._tools)

    def tool_specs(self) -> List[Dict[str, Any]]:
        """The allowed tools as chat-completions function specs, prefixed so they can't collide."""
        specs = []
        for name in self.tool_names:
            tool = self._tools[name]
            schema = tool.inputSchema if isinstance(tool.inputSchema, dict) else {}
            parameters: Dict[str, Any] = {"type": "object", "properties": schema.get("properties") or {}}
            if schema.get("required"):
                parameters["required"] = list(schema["required"])
            specs.append({"type": "function", "function": {
                "name": TOOL_PREFIX + name,
                "description": ("Confluence (read-only): " + (tool.description or name))[:_DESCRIPTION_LIMIT],
                "parameters": parameters,
            }})
        return specs

    @staticmethod
    def owns(name: str) -> bool:
        return name.startswith(TOOL_PREFIX)

    async def call(self, prefixed_name: str, args: Dict[str, Any]) -> str:
        name = prefixed_name[len(TOOL_PREFIX):]
        tool = self._tools.get(name)
        if tool is None:
            return f"'{name}' is not one of the read-only Confluence tools available to you."
        props = (tool.inputSchema or {}).get("properties") or {}
        if self.cloud_id and "cloudId" in props and not args.get("cloudId"):
            args = {**args, "cloudId": self.cloud_id}
        try:
            result = await self._session.call_tool(
                name, arguments=args, read_timeout_seconds=timedelta(seconds=_CALL_TIMEOUT_SECONDS),
            )
        except Exception as e:  # noqa: BLE001 - reported to the model, never fatal
            logger.warning("context review: Confluence %s failed: %s", name, e)
            return f"The Confluence call failed: {str(e)[:300]}"
        self.calls += 1
        text = result_text(result)
        if getattr(result, "isError", False):
            return "Confluence returned an error: " + text[:2000]
        if len(text) > _OUTPUT_LIMIT:
            return text[:_OUTPUT_LIMIT] + "\n... truncated; narrow the request for the rest."
        return text or "(empty result)"


def result_text(result: Any) -> str:
    parts = [getattr(p, "text", None) for p in getattr(result, "content", None) or []]
    text = "\n".join(p for p in parts if isinstance(p, str) and p).strip()
    if not text and isinstance(getattr(result, "structuredContent", None), dict):
        text = json.dumps(result.structuredContent, default=str)
    return text


def pick_site(resources: Any, wanted: str = "") -> Optional[Dict[str, Any]]:
    """The Atlassian site to search: the one matching ``wanted`` (URL, host or cloudId), else the first."""
    if isinstance(resources, dict):
        resources = resources.get("resources") or resources.get("sites") or [resources]
    sites = [r for r in resources or [] if isinstance(r, dict) and r.get("id")]
    if not sites:
        return None
    wanted = (wanted or "").strip().lower().rstrip("/")
    if wanted:
        wanted_host = urlparse(wanted if "://" in wanted else f"https://{wanted}").netloc
        for site in sites:
            host = urlparse(str(site.get("url") or "")).netloc.lower()
            if wanted in (str(site["id"]).lower(), str(site.get("url") or "").lower().rstrip("/")) or (
                wanted_host and host == wanted_host
            ):
                return site
    return sites[0]


def describe_failure(exc: BaseException, connection: str) -> str:
    """An admin-facing reason Confluence couldn't be used, naming the fix."""
    from app.providers.databricks_mcp.client import _http_status

    status = _http_status(exc)
    if status == 401:
        return ("Databricks refused the token for the Confluence connection. Reload the app so it can ask for "
                "the mcp.external permission, then review again.")
    if status == 403:
        return (f"Access to the Confluence connection '{connection}' was refused. You need USE CONNECTION on it, "
                "the app needs the mcp.external user scope, and on a per-user connection you must have signed "
                f"in to Atlassian once (Catalog → Connections → {connection}).")
    if status == 404:
        return (f"No Unity Catalog connection named '{connection}' was found. Check the Confluence MCP "
                "connection under Admin → Settings → Context Catalog → AI review.")
    return f"Couldn't reach Confluence through connection '{connection}': {str(exc)[:300]}"


async def _list_tools(session: Any) -> List[Any]:
    tools: List[Any] = []
    cursor = None
    for _ in range(_MAX_TOOL_PAGES):
        page = await session.list_tools(cursor) if cursor else await session.list_tools()
        tools.extend(page.tools or [])
        cursor = getattr(page, "nextCursor", None)
        if not cursor:
            break
    return tools


async def _resolve_site(session: ConfluenceSession, wanted: str) -> None:
    if "getAccessibleAtlassianResources" not in session.tool_names:
        return
    try:
        result = await session._session.call_tool(
            "getAccessibleAtlassianResources", arguments={},
            read_timeout_seconds=timedelta(seconds=_CALL_TIMEOUT_SECONDS),
        )
        site = pick_site(json.loads(result_text(result) or "[]"), wanted)
    except Exception as e:  # noqa: BLE001 - the model can still call the tool itself
        logger.info("context review: couldn't resolve the Atlassian site up front: %s", e)
        return
    if site:
        session.cloud_id = str(site["id"])
        session.site_url = site.get("url")


@asynccontextmanager
async def open_confluence(obo_token: Optional[str]) -> AsyncIterator[ConfluenceAccess]:
    """Yield Confluence access for one review; never raises for a Confluence problem."""
    connection = (getattr(settings, "CONTEXT_CATALOG_CONFLUENCE_CONNECTION", "") or "").strip()
    if not connection:
        yield ConfluenceAccess("not_configured", "No Confluence MCP connection is set, so the document was "
                                                 "checked against the catalog only.")
        return

    from app.providers.databricks_mcp import build_external_mcp_url, open_mcp_session, resolve_genie_bearer_token

    try:
        token, _source = resolve_genie_bearer_token(obo_token)
    except Exception as e:  # noqa: BLE001 - review without Confluence rather than fail
        logger.warning("context review: no token for Confluence: %s", e)
        token = None
    if not token:
        yield ConfluenceAccess("unavailable", "No user token was available to call Confluence as you. Reload "
                                              "the app and review again.")
        return

    stack = AsyncExitStack()
    access: ConfluenceAccess
    try:
        url = build_external_mcp_url(connection)
        raw = await stack.enter_async_context(open_mcp_session(url, token, timeout_seconds=30, sse_read_timeout=120))
        wrapped = ConfluenceSession(raw, await _list_tools(raw))
        if not wrapped.tool_names:
            raise RuntimeError("the server offers none of the read-only Confluence tools")
        await _resolve_site(wrapped, getattr(settings, "CONTEXT_CATALOG_CONFLUENCE_SITE", "") or "")
        access = ConfluenceAccess("available", session=wrapped)
    except Exception as e:  # noqa: BLE001 - review without Confluence rather than fail
        logger.warning("context review: Confluence unavailable via %s: %s", connection, e)
        try:
            await stack.aclose()
        except Exception:  # noqa: BLE001
            pass
        yield ConfluenceAccess("unavailable", describe_failure(e, connection))
        return
    try:
        yield access
    finally:
        try:
            await stack.aclose()
        except Exception as e:  # noqa: BLE001 - closing a finished session must not fail the review
            logger.debug("context review: closing the Confluence session failed: %s", e)


__all__ = [
    "ConfluenceAccess",
    "ConfluenceSession",
    "READ_ONLY_TOOLS",
    "TOOL_PREFIX",
    "describe_failure",
    "open_confluence",
    "pick_site",
]
