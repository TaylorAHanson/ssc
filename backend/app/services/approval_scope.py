"""
Who is allowed to see which approval — in one place.

This filter used to be written out three times (the approvals API, the
``search_approvals`` agent tool, and now the user-context builder). The copies
had already drifted: one matched roles via ``User.has_role`` (which normalizes
case and underscores) while the other lowercased a comma-separated string, so a
role spelled ``platform_admin`` resolved in one path and not the other. Callers
now normalize their identity into plain strings and share the rules below.
"""
from typing import Any, Iterable, List, Optional, Sequence

from sqlalchemy import or_

from app.db import ApprovalModel

# An approval is visible to a role either because it is addressed to that role
# directly (``assigned_to_role``) or because the role owns that whole class of
# approval. Platform Admin sees everything, mirroring ``User.has_role``.
#
# ``manual_task`` must stay in the Platform Admin list. A manual task is created
# with whatever assignee the gate resolved, and a gate that names no approver
# resolves to none at all — so without a role that owns the type, the row is
# addressed to nobody, appears in nobody's inbox, and the request waits at the
# gate forever. ``_authorize_approval_actor`` already grants platform admins the
# break-glass action on an unassigned approval; this is the matching visibility
# so they can find it.
ROLE_APPROVAL_TYPES = {
    "platform admin": (
        "platform_admin", "manager", "data_owner", "security",
        "security_admin", "finance_admin", "governance_admin", "manual_task",
    ),
    "governance admin": ("governance_admin",),
    "security admin": ("security", "security_admin"),
    "finance admin": ("finance_admin",),
}

PLATFORM_ADMIN = "platform admin"


def normalize_role(role: Any) -> str:
    """Fold a role to the canonical lowercase, space-separated form.

    ``Platform Admin``, ``platform_admin`` and ``PLATFORM ADMIN`` are the same
    role; they arrive in all three spellings depending on whether the value came
    from ``role_mappings``, an OPA input, or an injected tool kwarg.
    """
    return str(role or "").strip().lower().replace("_", " ")


def parse_csv(value: Optional[str]) -> List[str]:
    """Split an injected ``_user_roles`` / ``_user_entitlements`` kwarg.

    The ToolExecutor flattens these to comma-separated strings, while the API
    layer has real lists — this is the adapter for the former.
    """
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def normalize_roles(roles: Optional[Iterable[Any]]) -> List[str]:
    return [normalize_role(r) for r in (roles or []) if normalize_role(r)]


def is_platform_admin(roles: Optional[Iterable[Any]]) -> bool:
    return PLATFORM_ADMIN in normalize_roles(roles)


def allowed_approval_types(roles: Optional[Iterable[Any]]) -> List[str]:
    """Approval types the given roles may act on, deduplicated."""
    out: List[str] = []
    for role in normalize_roles(roles):
        for approval_type in ROLE_APPROVAL_TYPES.get(role, ()):
            if approval_type not in out:
                out.append(approval_type)
    return out


def approval_visibility_filter(
    email: Optional[str],
    roles: Optional[Iterable[Any]] = None,
    entitlements: Optional[Sequence[str]] = None,
):
    """SQLAlchemy filter for the approvals one user is entitled to see.

    Visible when the approval is assigned to them, delegated to them, addressed
    to a group they belong to, or of a type their role owns. Apply to a query
    that already joins ``ApprovalModel``.
    """
    return or_(
        ApprovalModel.assigned_to_email == email,
        ApprovalModel.delegated_to_email == email,
        ApprovalModel.assigned_to_role.in_(list(entitlements or [])),
        ApprovalModel.approval_type.in_(allowed_approval_types(roles)),
    )
