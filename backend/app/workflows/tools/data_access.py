"""
Data access and ownership resolution workflow tools.
"""
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool
from app.workflows.tools import _common

logger = logging.getLogger(__name__)


class GrantUcAccessInput(BaseModel):
    asset_type: str = Field(..., description="schema | table | view | volume")
    asset_name: str = Field(..., description="Fully-qualified UC name")
    principal: str = Field(..., description="User/group to grant access to")
    access_level: str = Field(..., description="read | write | manage")


@tool(
    name="grant_uc_access",
    args_schema=GrantUcAccessInput,
    side_effect_class="data_grant",
    description="Grant a principal access to a Unity Catalog asset via SQL GRANT.",
)
async def grant_uc_access(
    asset_type: str,
    asset_name: str,
    principal: str,
    access_level: str,
    **kwargs,
) -> Dict[str, Any]:
    provider = _common._get_databricks_provider()
    result = await provider.grant_access(
        asset_type=asset_type,
        asset_name=asset_name,
        principal=principal,
        access_level=access_level,
    )
    return {"asset_name": asset_name, "result": result}


class ResolveDataOwnersInput(BaseModel):
    assets: Optional[Any] = Field(
        default=None,
        description="Assets: a list of {asset_name, asset_type}, a single such dict, or a bare asset-name string.",
    )
    asset_name: Optional[str] = Field(
        default=None,
        description="Single asset name (backwards-compat) when `assets` isn't a list.",
    )
    asset_type: Optional[str] = Field(
        default=None,
        description="Single asset type for the backwards-compat single-asset form.",
    )
    data_owners: Optional[Any] = Field(
        default=None,
        description="Pre-supplied owners — honored only when it's a real list of strings.",
    )


def _normalize_assets(
    assets: Any,
    asset_name: Optional[str] = None,
    asset_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Coerce agent-supplied asset params into ``[{asset_name, asset_type}, ...]``."""
    out: List[Dict[str, Any]] = []
    if isinstance(assets, list):
        for a in assets:
            if isinstance(a, dict) and a.get("asset_name"):
                out.append({"asset_name": a.get("asset_name"), "asset_type": a.get("asset_type") or asset_type})
            elif isinstance(a, str) and a:
                out.append({"asset_name": a, "asset_type": asset_type})
    elif isinstance(assets, dict) and assets.get("asset_name"):
        out.append({"asset_name": assets.get("asset_name"), "asset_type": assets.get("asset_type") or asset_type})
    elif isinstance(assets, str) and assets:
        out.append({"asset_name": assets, "asset_type": asset_type})
    if not out and asset_name:
        out.append({"asset_name": asset_name, "asset_type": asset_type})
    return out


async def resolve_owner_groups_from_assets(
    assets: Optional[List[Dict[str, Any]]],
    *,
    fallback_to_owner: bool = True,
) -> List[str]:
    """Resolve approver group(s) for ``assets`` from the UC ``approver_group`` tag."""
    if not assets:
        return []
    from app.core.config import settings

    tag_key = settings.APPROVER_GROUP_TAG_KEY
    found: set = set()
    try:
        provider = _common._get_databricks_provider()
        for asset in assets:
            name, atype = asset.get("asset_name"), asset.get("asset_type")
            if not (name and atype):
                continue
            tags = await provider.get_asset_tags(atype, name, [tag_key])
            grp = tags.get(tag_key)
            if grp:
                found.add(grp)
            elif fallback_to_owner:
                owner = await provider.get_asset_owner(atype, name)
                if owner:
                    found.add(owner)
    except Exception as e:  # noqa: BLE001 - degrade gracefully like the old graph
        logger.warning("resolve_owner_groups_from_assets degraded: %s", e)
    return sorted(found)


@tool(
    name="resolve_data_owners",
    args_schema=ResolveDataOwnersInput,
    side_effect_class="read",
    description="Resolve the data-owner approver group(s) for the requested assets from UC tags (approver_group), falling back to the asset owner. Read-only.",
)
async def resolve_data_owners(
    assets: Any = None,
    data_owners: Any = None,
    asset_name: Optional[str] = None,
    asset_type: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Owner resolution for data-access gates, as a tool."""
    owners = [o for o in data_owners if isinstance(o, str)] if isinstance(data_owners, list) else []
    if not owners:
        norm = _normalize_assets(assets, asset_name, asset_type)
        owners = await resolve_owner_groups_from_assets(norm)
    return {"ok": True, "data_owners": owners}
