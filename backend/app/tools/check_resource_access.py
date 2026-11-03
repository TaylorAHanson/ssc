"""
Tool to check existing access grants on a Unity Catalog securable.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from app.core.workspaces import get_uc_provider
from app.tools.mcp import tool
from app.tools.sql_safety import SqlSafetyError, require_identifier

logger = logging.getLogger(__name__)

ResourceType = Literal["CATALOG", "SCHEMA", "TABLE", "VOLUME", "FUNCTION", "MODEL"]

# How many dot-separated parts a full name of each type has.
_NAME_PARTS: Dict[str, int] = {
    "CATALOG": 1, "SCHEMA": 2, "TABLE": 3, "VOLUME": 3, "FUNCTION": 3, "MODEL": 3,
}
_TYPE_BY_PARTS: Dict[int, str] = {1: "CATALOG", 2: "SCHEMA", 3: "TABLE"}


class CheckResourceAccessInput(BaseModel):
    resource_name: str = Field(
        ...,
        description=(
            "Full name of the Unity Catalog securable: 'catalog', 'catalog.schema', or "
            "'catalog.schema.object' for a table, view, volume, function or model."
        ),
    )
    resource_type: Optional[ResourceType] = Field(
        default=None,
        description=(
            "Type of the securable. Optional: inferred from the name (1 part CATALOG, "
            "2 parts SCHEMA, 3 parts TABLE, which also covers views). Set it for a "
            "VOLUME, FUNCTION or MODEL."
        ),
    )
    target_host: Optional[str] = Field(
        default=None,
        description="Workspace host for context only. Unity Catalog is account-global, so grants are always read from the local workspace regardless of this value.",
    )
    principal: Optional[str] = Field(
        default=None,
        description="Optional: specific user, group, or service principal to check",
    )


def _norm_key(name: str) -> str:
    return name.replace("`", "").strip().lower()


@tool(
    name="check_resource_access",
    description=(
        "Inspect live Unity Catalog grants (SHOW GRANTS) on one catalog, schema, table, "
        "view, volume, function or model, as the signed-in user. Includes privileges "
        "inherited from the parent schema or catalog. Use before granting or revoking "
        "access to verify current state, or to see which groups hold access to an object."
    ),
    args_schema=CheckResourceAccessInput,
    side_effect_class="read",
)
async def check_resource_access(
    resource_name: str,
    resource_type: Optional[str] = None,
    target_host: Optional[str] = None,
    principal: Optional[str] = None,
    _obo_token: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Check what access grants exist on a resource by querying Unity Catalog.
    """
    parts = [p.strip() for p in resource_name.split(".")]
    try:
        for part in parts:
            require_identifier(part, "resource_name")
    except SqlSafetyError as e:
        return {"success": False, "error": str(e)}

    object_type = (resource_type or "").upper() or _TYPE_BY_PARTS.get(len(parts))
    if object_type not in _NAME_PARTS or _NAME_PARTS[object_type] != len(parts):
        expected = (
            f"a {object_type} name has {_NAME_PARTS[object_type]} part(s)"
            if object_type in _NAME_PARTS
            else "use 'catalog', 'catalog.schema' or 'catalog.schema.object'"
        )
        return {
            "success": False,
            "error": "invalid_resource_name",
            "message": f"Could not check access on '{resource_name}': {expected}.",
        }
    resource_name = ".".join(parts)

    # SHOW GRANTS must run as the signed-in user so the answer reflects what
    # they may see; only true local dev may fall back to the app service principal.
    from app.providers.databricks_mcp import sp_fallback_allowed

    if not _obo_token and not sp_fallback_allowed():
        logger.warning(
            "check_resource_access refused for '%s': no user token was forwarded.",
            resource_name,
        )
        return {
            "success": False,
            "error": "missing_user_token",
            "message": (
                f"Could not check access on '{resource_name}': your sign-in token "
                "wasn't passed along with this request, and grants are only read "
                "as you, never as the app. Refresh the page or sign in again, "
                "then retry."
            ),
        }

    try:
        provider = get_uc_provider()

        query = f"SHOW GRANTS ON {object_type} {resource_name}"
        result = await provider.execute_sql(
            query,
            timeout_seconds=30,
            obo_token=_obo_token,
            require_obo=True,
        )
        rows = result.get("rows", [])

        own_key = _norm_key(resource_name)
        grants: List[Dict[str, Any]] = []
        for r in rows:
            p = r.get("Principal") or r.get("principal")
            action = r.get("ActionType") or r.get("action_type") or r.get("Privilege")
            if not (p and action):
                continue
            existing = next((g for g in grants if g["principal"] == p), None)
            if existing is None:
                existing = {"principal": p, "privileges": []}
                grants.append(existing)
            if action not in existing["privileges"]:
                existing["privileges"].append(action)
            # SHOW GRANTS on a child object also lists what the parent grants.
            granted_type = r.get("ObjectType") or r.get("object_type")
            granted_key = r.get("ObjectKey") or r.get("object_key")
            if granted_key and _norm_key(str(granted_key)) != own_key:
                existing.setdefault("inherited_from", {})[action] = f"{granted_type or ''} {granted_key}".strip()

        if principal:
            principal_match = next(
                (g for g in grants if g["principal"].lower() == principal.lower()),
                None,
            )
            privs = principal_match["privileges"] if principal_match else []
            out = {
                "success": True,
                "exists": True,
                "resource_name": resource_name,
                "resource_type": object_type,
                "principal": principal,
                "principal_privileges": privs,
                "has_grants": bool(privs),
                "message": (
                    f"{principal} has privileges: {', '.join(privs)}"
                    if privs
                    else f"{principal} has no grants on {resource_name}"
                ),
            }
            if principal_match and principal_match.get("inherited_from"):
                out["inherited_from"] = principal_match["inherited_from"]
            return out

        grant_summary = [f"{g['principal']}: {', '.join(g['privileges'])}" for g in grants]
        return {
            "success": True,
            "exists": True,
            "resource_name": resource_name,
            "resource_type": object_type,
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
