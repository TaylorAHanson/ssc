"""
Tool to check existing access grants on a Unity Catalog resource.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.config import settings
from app.providers.databricks import DatabricksProvider
from app.tools.mcp import tool
from app.tools.sql_safety import SqlSafetyError, require_identifier

logger = logging.getLogger(__name__)


class CheckResourceAccessInput(BaseModel):
    resource_name: str = Field(
        ...,
        description="Full name of the Unity Catalog resource (e.g. 'main.default' or 'sales_catalog')",
    )
    target_host: Optional[str] = Field(
        default=None,
        description="The host URL of the target Databricks workspace (optional).",
    )
    principal: Optional[str] = Field(
        default=None,
        description="Optional: specific user, group, or service principal to check",
    )


@tool(
    name="check_resource_access",
    description="Inspect live Unity Catalog access grants on a catalog or schema (e.g. 'main' or 'main.sales'). Use before granting or revoking access to verify current state.",
    args_schema=CheckResourceAccessInput,
    side_effect_class="read",
)
async def check_resource_access(
    resource_name: str,
    target_host: Optional[str] = None,
    principal: Optional[str] = None,
    _obo_token: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Check what access grants exist on a resource by querying Unity Catalog.
    """
    try:
        for part in resource_name.split("."):
            require_identifier(part.strip(), "resource_name")
    except SqlSafetyError as e:
        return {"success": False, "error": str(e)}

    try:
        provider = DatabricksProvider(
            host=target_host or settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID},
        )

        is_schema = "." in resource_name
        object_type = "SCHEMA" if is_schema else "CATALOG"

        query = f"SHOW GRANTS ON {object_type} {resource_name}"
        result = await provider.execute_sql(
            query,
            timeout_seconds=30,
            obo_token=_obo_token,
            require_obo=False,
        )
        rows = result.get("rows", [])

        grants: List[Dict[str, Any]] = []
        for r in rows:
            p = r.get("Principal") or r.get("principal")
            action = r.get("ActionType") or r.get("action_type") or r.get("Privilege")
            if p and action:
                existing = next((g for g in grants if g["principal"] == p), None)
                if existing:
                    if action not in existing["privileges"]:
                        existing["privileges"].append(action)
                else:
                    grants.append({"principal": p, "privileges": [action]})

        if principal:
            principal_match = next(
                (g for g in grants if g["principal"].lower() == principal.lower()),
                None,
            )
            privs = principal_match["privileges"] if principal_match else []
            return {
                "success": True,
                "exists": True,
                "resource_name": resource_name,
                "principal": principal,
                "principal_privileges": privs,
                "has_grants": bool(privs),
                "message": (
                    f"{principal} has privileges: {', '.join(privs)}"
                    if privs
                    else f"{principal} has no grants on {resource_name}"
                ),
            }

        grant_summary = [f"{g['principal']}: {', '.join(g['privileges'])}" for g in grants]
        return {
            "success": True,
            "exists": True,
            "resource_name": resource_name,
            "grants": grants,
            "message": (
                f"Grants on {resource_name}: {'; '.join(grant_summary)}"
                if grant_summary
                else f"No grants found on {resource_name}"
            ),
        }
    except Exception as e:
        logger.error(f"Error checking resource access for '{resource_name}': {e}")
        return {
            "success": False,
            "error": str(e),
            "message": f"Could not check access on '{resource_name}': {e}",
        }
