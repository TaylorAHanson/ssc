"""Who approves and who edits each type of rule (ADR 0005 D6, D8).

Each type (plane) has one Databricks group, named in settings. Its members
approve that type's requests (the poller routes the approval to the group via
``assigned_to_role``) and edit that type's rows directly. Platform Admins can do
both for every type. Viewer roles see everything and edit nothing.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

from app.core.config import settings
from app.db.rbac import PLANE_METASTORE, PLANE_UC, PLANE_WORKSPACE, PLANES
from app.models.rbac import PlanePermission, RbacPermissions
from app.models.user import User
from app.services.approval_scope import in_group
from app.services.rbac.layout import PLANE_LABELS

logger = logging.getLogger(__name__)

PLATFORM_ADMIN = "Platform Admin"

_GROUP_SETTINGS: Dict[str, str] = {
    PLANE_UC: "RBAC_UC_APPROVER_GROUP",
    PLANE_METASTORE: "RBAC_METASTORE_APPROVER_GROUP",
    PLANE_WORKSPACE: "RBAC_WS_APPROVER_GROUP",
}

GROUP_CACHE_SECONDS = 300.0
# Exact group name -> (looked up at, status). Failed lookups aren't cached.
_group_cache: Dict[str, Tuple[float, str]] = {}

# The account's groups, served by the workspace. Unlike the workspace's own group
# list it includes account groups that aren't assigned to the workspace.
ACCOUNT_GROUPS_PATH = "/api/2.0/account/scim/v2/Groups"


class RbacForbidden(Exception):
    """The user may not perform this Access Rules action."""


def approver_group(plane: str) -> Optional[str]:
    """The configured group display name for a plane, or None when unset."""
    key = _GROUP_SETTINGS.get(plane)
    if not key:
        return None
    name = str(getattr(settings, key, "") or "").strip()
    return name or None


def approval_label(plane: Optional[str], group: Optional[str]) -> str:
    """``UC securables approvers: grp_x``, or ``Platform Admins (no UC securables group set)``."""
    label = PLANE_LABELS.get(plane or "")
    if group:
        return f"{label} approvers: {group}" if label else f"Access rule approvers: {group}"
    return f"Platform Admins (no {label} group set)" if label else "Platform Admins"


def is_member(user: User, group: Optional[str]) -> bool:
    """Whether ``group`` is among the user's Databricks groups (``user.entitlements``), case-insensitive."""
    return in_group(user.entitlements, group)


def _is_platform_admin(user: User) -> bool:
    return user.has_role(PLATFORM_ADMIN)


def can_edit(user: User, plane: str) -> bool:
    return _is_platform_admin(user) or is_member(user, approver_group(plane))


def editable_planes(user: User) -> List[str]:
    return [plane for plane in PLANES if can_edit(user, plane)]


def can_view(user: User) -> bool:
    """Platform Admin, a viewer role, or a member of any approver group."""
    if _is_platform_admin(user):
        return True
    if any(role and user.has_role(role) for role in (settings.RBAC_VIEWER_ROLES or [])):
        return True
    return any(is_member(user, approver_group(plane)) for plane in PLANES)


def require_view(user: User) -> None:
    """Raise RbacForbidden unless ``can_view``."""
    if not can_view(user):
        raise RbacForbidden(
            "You don't have access to Access Rules. Ask a Platform Admin to add you to an approver "
            "group or a viewer role."
        )


def require_edit(user: User, plane: str) -> None:
    """Raise RbacForbidden unless ``can_edit`` for the plane."""
    if not can_edit(user, plane):
        label = PLANE_LABELS.get(plane, plane)
        group = approver_group(plane)
        who = f"members of {group} and Platform Admins" if group else "Platform Admins"
        raise RbacForbidden(f"Only {who} can change {label} rules.")


def _scim_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _lookup_group(name: str) -> str:
    from databricks.sdk import WorkspaceClient

    client = WorkspaceClient()
    scim_filter = f'displayName eq "{_scim_string(name)}"'
    if next(iter(client.groups.list(filter=scim_filter, attributes="id,displayName")), None) is not None:
        return "found"
    try:
        res = client.api_client.do(
            "GET", ACCOUNT_GROUPS_PATH,
            query={"filter": scim_filter, "attributes": "id,displayName", "count": 1},
        ) or {}
    except Exception as exc:  # noqa: BLE001 - e.g. a workspace without identity federation
        logger.info("Couldn't check account groups for %r; only this workspace's were checked: %s", name, exc)
        return "unverified"
    return "account_only" if res.get("Resources") else "not_found"


async def group_status(name: str) -> str:
    """What Databricks says about a group name, as the app's identity sees it. Cached briefly.

    ``found`` (assigned to this workspace); ``account_only`` (an account group not
    assigned to this workspace); ``not_found`` (no account group has this name);
    ``unverified`` (not in this workspace, and the account couldn't be checked);
    ``unknown`` (the lookup failed, and isn't cached).
    """
    if not name or not name.strip():
        return "not_found"
    now = time.monotonic()
    cached = _group_cache.get(name)
    if cached and now - cached[0] < GROUP_CACHE_SECONDS:
        return cached[1]
    try:
        status = await asyncio.to_thread(_lookup_group, name)
    except Exception as exc:  # noqa: BLE001 - any SDK/auth/network failure means "unknown"
        logger.warning("Couldn't check whether Databricks group %r exists: %s", name, exc)
        return "unknown"
    _group_cache[name] = (time.monotonic(), status)
    return status


async def _plane_group_status(group: Optional[str], user: User) -> str:
    if not group:
        return "unset"
    status = await group_status(group)
    # The viewer's own groups come from Databricks too, so membership confirms the name.
    if status in ("not_found", "unverified", "unknown") and is_member(user, group):
        return "found"
    return status


async def permissions(user: User) -> RbacPermissions:
    """The current user's rights plus each plane's group and whether Databricks confirms it."""
    groups = [approver_group(plane) for plane in PLANES]
    statuses = await asyncio.gather(*(_plane_group_status(g, user) for g in groups))
    planes = [
        PlanePermission(
            plane=plane,
            label=PLANE_LABELS.get(plane, plane),
            approver_group=group,
            group_status=status,
            can_edit=can_edit(user, plane),
        )
        for plane, group, status in zip(PLANES, groups, statuses)
    ]
    is_admin = _is_platform_admin(user)
    return RbacPermissions(
        can_view=can_view(user),
        can_import=is_admin,
        can_reset=is_admin and bool(settings.RBAC_ALLOW_RESET),
        can_export=is_admin or any(p.can_edit for p in planes),
        planes=planes,
        min_reason_length=int(settings.RBAC_MIN_REASON_LENGTH or 0),
        max_changes_per_commit=int(settings.RBAC_MAX_CHANGES_PER_COMMIT or 0),
    )
