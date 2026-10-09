"""Data asset owner resolution service.

Unity Catalog table/view objects frequently have a technical service principal
or administrative identity as their direct object `owner`. When a governed tag
(by default `data_owner`, configurable via `DATA_ASSET_OWNER_TAG` in Admin -> Settings)
is defined on the asset, its value represents the accountable human or team owner
and is preferred for display across the data catalog and agent tools.

If `DATA_ASSET_OWNER_TAG` is empty or not set, or if the asset does not carry the
specified tag, resolution falls back to the direct `owner` parameter.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


def resolve_data_asset_owner(
    owner: Optional[str],
    tags: Any,
    owner_tag: Optional[str] = None,
) -> Optional[str]:
    """Resolve the accountable owner for a data asset.

    Args:
        owner: The direct Unity Catalog owner (e.g. SP UUID, admin user, or None).
        tags: The tags associated with the asset. Can be a list of string tags
              (e.g. ["data_owner=supply-chain-team", "certified"]), a dict
              (e.g. {"data_owner": "supply-chain-team"}), a JSON-encoded string,
              or a list of tag dictionaries.
        owner_tag: Tag key to look for. If None, uses `settings.DATA_ASSET_OWNER_TAG`.
                   If empty string, resolution is bypassed and `owner` is returned.

    Returns:
        The tag value if found and non-empty, otherwise `owner`.
    """
    configured_tag = (
        owner_tag
        if owner_tag is not None
        else getattr(settings, "DATA_ASSET_OWNER_TAG", "data_owner")
    )
    if not configured_tag:
        return owner

    target_key = configured_tag.strip().lower()
    if not target_key:
        return owner

    # Parse JSON string if necessary
    parsed_tags = tags
    if isinstance(parsed_tags, str):
        try:
            parsed_tags = json.loads(parsed_tags)
        except (ValueError, TypeError):
            # Not valid JSON; could be a single string tag like "data_owner=team"
            parsed_tags = [parsed_tags]

    # Handle dictionary of tags: {"data_owner": "Team Name", ...}
    if isinstance(parsed_tags, dict):
        for k, v in parsed_tags.items():
            if str(k).strip().lower() == target_key and v is not None:
                val = str(v).strip().strip("'\"")
                if val:
                    return val
        return owner

    # Handle list/collection of tags: ["data_owner=Team Name", ...]
    # or [{"tag_name": "...", "tag_value": "..."}, ...]
    if isinstance(parsed_tags, (list, tuple, set)):
        for item in parsed_tags:
            if isinstance(item, dict):
                # Common shapes: {"tag_name": k, "tag_value": v} or {"name": k, "value": v}
                name = item.get("tag_name") or item.get("name") or item.get("key")
                val = item.get("tag_value") or item.get("value")
                if name is not None and str(name).strip().lower() == target_key and val is not None:
                    cleaned_val = str(val).strip().strip("'\"")
                    if cleaned_val:
                        return cleaned_val
                for k, v in item.items():
                    if str(k).strip().lower() == target_key and v is not None:
                        cleaned_val = str(v).strip().strip("'\"")
                        if cleaned_val:
                            return cleaned_val
            elif isinstance(item, str):
                s = item.strip()
                # Check key=value
                if "=" in s:
                    key, val = s.split("=", 1)
                    if key.strip().lower() == target_key:
                        cleaned_val = val.strip().strip("'\"")
                        if cleaned_val:
                            return cleaned_val
                # Check key:value
                elif ":" in s:
                    key, val = s.split(":", 1)
                    if key.strip().lower() == target_key:
                        cleaned_val = val.strip().strip("'\"")
                        if cleaned_val:
                            return cleaned_val

    return owner
