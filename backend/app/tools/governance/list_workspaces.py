"""
Tool to list workspaces.

Answers from the local workspace cache (``app.services.workspace_cache``, filled
by the data-asset sync as the governance service principal) while it's fresh.
Otherwise, or on ``refresh``, it runs the live query On-Behalf-Of the user.
"""
import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Literal
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.services import workspace_cache

logger = logging.getLogger(__name__)

class ListWorkspacesInput(BaseModel):
    status: Optional[Literal["RUNNING", "PROVISIONING", "FAILED", "BANNED", "NOT_PROVISIONED"]] = Field(None, description="Optional filter to list workspaces only with a specific status.")
    name_contains: Optional[str] = Field(
        None,
        description=(
            "Only workspaces whose name or URL contains this text, ignoring case, spaces, dashes "
            "and underscores (so 'supply chain' matches 'ws-supplychain-prod'). Use it whenever "
            "you're looking for particular workspaces; accounts can have thousands."
        ),
    )
    limit: int = Field(100, ge=1, le=1000, description="Maximum number of workspaces to return.")
    refresh: bool = Field(
        False,
        description=(
            "Skip the cache and query the account's workspaces live. Slow (can take a minute or "
            "more); only set it when a workspace may have been created or deleted after the "
            "cached_at time, or the user asks for the very latest list."
        ),
    )


def _squash(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _select(rows: List[Dict[str, Any]], name_contains: Optional[str], limit: int) -> Dict[str, Any]:
    needle = _squash(name_contains)
    if needle:
        rows = [
            r for r in rows
            if needle in _squash(r.get("workspace_name")) or needle in _squash(r.get("workspace_url"))
        ]
    out: Dict[str, Any] = {"workspaces": rows[:limit], "count": min(len(rows), limit)}
    if len(rows) > limit:
        out["total_matches"] = len(rows)
        out["truncated"] = True
    return out


def _cache_result(
    rows: List[Dict[str, Any]], cached_at: datetime, name_contains: Optional[str], limit: int
) -> Dict[str, Any]:
    return {
        **_select(rows, name_contains, limit),
        "source": "cache",
        "cached_at": cached_at.isoformat(),
    }


@tool(
    name="list_workspaces",
    description=(
        "List all Databricks workspaces in the account with IDs, names, URLs, and status. Fast: "
        "answers from a cache the app refreshes in the background (`source: cache`, with "
        "`cached_at`), so use it freely, e.g. to check whether a workspace name already exists; "
        "pass name_contains to narrow the list. Pass refresh=true only when the answer depends "
        "on a change made after `cached_at`; that runs a slow live query."
    ),
    args_schema=ListWorkspacesInput
)
async def list_workspaces(
    status: Optional[str] = None,
    name_contains: Optional[str] = None,
    limit: int = 100,
    refresh: bool = False,
    **kwargs,
) -> Dict[str, Any]:
    cached_rows: List[Dict[str, Any]] = []
    cached_at: Optional[datetime] = None
    if workspace_cache.enabled():
        try:
            cached_rows, cached_at = await asyncio.to_thread(
                workspace_cache.read_cached_workspaces, status
            )
        except Exception as e:  # noqa: BLE001 - the live query below still answers
            logger.warning("Workspace cache unreadable; querying live instead: %s", e)
    if not refresh and workspace_cache.is_fresh(cached_at):
        return _cache_result(cached_rows, cached_at, name_contains, limit)

    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )

        where_clause = ""
        if status:
            where_clause = f"WHERE status = '{status}'"
        
        query = f"""
            SELECT 
                account_id,
                workspace_id,
                workspace_name,
                workspace_url,
                create_time,
                status
            FROM system.access.workspaces_latest
            {where_clause}
            ORDER BY workspace_name ASC
        """
        
        # Run as the user (OBO) so their own access to system.access.* applies —
        # the app's service principal typically can't read system tables.
        obo_token = kwargs.get("_obo_token")
        result = await provider.execute_sql(query, timeout_seconds=120, obo_token=obo_token, require_obo=True)
        
        return {
            **_select(result.get("rows", []), name_contains, limit),
            "source": "live",
            "query": query
        }
    except Exception as e:
        if cached_at is not None:
            logger.warning("Live workspace query failed; answering from the cache: %s", e)
            out = _cache_result(cached_rows, cached_at, name_contains, limit)
            if not workspace_cache.is_fresh(cached_at):
                out["stale"] = True
            out["live_error"] = (
                f"The live query failed ({e}), so this is the cached list from {out['cached_at']}."
            )
            return out
        return {
            "error": f"Failed to list workspaces: {str(e)}",
            "workspaces": []
        }
