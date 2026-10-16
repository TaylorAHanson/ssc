"""
Approver-group resolution for gates whose approvers come from UC asset tags.
"""
import logging
from typing import Any, Dict, List, Optional

from app.workflows.tools import _common

logger = logging.getLogger(__name__)


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
    except Exception as e:  # noqa: BLE001 - a gate must still open without approvers
        logger.warning("resolve_owner_groups_from_assets degraded: %s", e)
    return sorted(found)
