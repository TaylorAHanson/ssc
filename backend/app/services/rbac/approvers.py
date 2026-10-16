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
# Exact group name -> (looked up at, exists). Failed lookups aren't cached.
_group_cache: Dict[str, Tuple[float, bool]] = {}


class RbacForbidden(Exception):
    """The user may not perform this Access Rules action."""


def approver_group(plane: str) -> Optional[str]:
    """The configured group display name for a plane, or None when unset."""
    key = _GROUP_SETTINGS.get(plane)
    if not key:
        return None
    name = str(getattr(settings, key, "") or "").strip()
    return name or None


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


def _lookup_group(name: str) -> bool:
    from databricks.sdk import WorkspaceClient

    groups = WorkspaceClient().groups.list(
        filter=f'displayName eq "{_scim_string(name)}"', attributes="id,displayName",
    )
    return next(iter(groups), None) is not None


async def group_exists(name: str) -> Optional[bool]:
    """True / False when Databricks says, None when the lookup failed. Cached briefly."""
    if not name or not name.strip():
        return False
    now = time.monotonic()
    cached = _group_cache.get(name)
    if cached and now - cached[0] < GROUP_CACHE_SECONDS:
        return cached[1]
    try:
        found = await asyncio.to_thread(_lookup_group, name)
    except Exception as exc:  # noqa: BLE001 - any SDK/auth/network failure means "unknown"
        logger.warning("Couldn't check whether Databricks group %r exists: %s", name, exc)
        return None
    _group_cache[name] = (time.monotonic(), found)
    return found


async def _group_status(group: Optional[str]) -> str:
    if not group:
        return "unset"
    exists = await group_exists(group)
    if exists is None:
        return "unknown"
    return "found" if exists else "not_found"


async def permissions(user: User) -> RbacPermissions:
    """The current user's rights plus each plane's group and whether it exists."""
    groups = [approver_group(plane) for plane in PLANES]
    statuses = await asyncio.gather(*(_group_status(g) for g in groups))
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
