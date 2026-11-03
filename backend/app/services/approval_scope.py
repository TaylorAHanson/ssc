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

from sqlalchemy import and_, func, or_

from app.core import capabilities
from app.db import ApprovalModel

# Every approval type a role owns outright (sees in its inbox whoever it's addressed to).
# Super Admin owns them all. The ``platform_admin`` type key is stored in workflow
# JSON and on approval rows and is shown as "Application Admin"; it is not renamed.
ALL_APPROVAL_TYPES = (
    "platform_admin", "manager", "data_owner", "security",
    "security_admin", "finance_admin", "governance_admin", "manual_task",
)

ROLE_APPROVAL_TYPES = {
    "super admin": ALL_APPROVAL_TYPES,
    "application admin": ("platform_admin",),
    "governance admin": ("governance_admin",),
    "security admin": ("security", "security_admin"),
    "finance admin": ("finance_admin",),
}

# ``manual_task`` with nobody on it. A gate that names no approver resolves to none
# at all, so without a role that owns these the row is addressed to nobody, appears
# in nobody's inbox, and the request waits at the gate forever. Application Admins
# see (and may complete) them, as Platform Admins did; Super Admins see every manual
# task via ALL_APPROVAL_TYPES.
UNASSIGNED_TASK_TYPE = "manual_task"
UNASSIGNED_TASK_CAPABILITY = "approvals.application"


def normalize_role(role: Any) -> str:
    """Fold a role to the canonical lowercase, space-separated form.

    ``Governance Admin``, ``governance_admin`` and ``GOVERNANCE ADMIN`` are the
    same role; they arrive in all three spellings depending on whether the value
    came from ``role_mappings``, an OPA input, or an injected tool kwarg. The old
    name ``Platform Admin`` folds to ``application admin``.
    """
    return capabilities.canonical_role(role).strip().lower().replace("_", " ")


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


def normalize_group(name: Any) -> str:
    """Fold a Databricks group display name for comparison (trimmed, case-insensitive)."""
    return str(name or "").strip().lower()


def in_group(entitlements: Optional[Iterable[Any]], group: Any) -> bool:
    """Whether ``group`` is among the user's entitlements (email + Databricks groups).

    A gate routes an approval to a group by storing its display name in
    ``assigned_to_role``; this is the membership test for seeing and acting on it.
    """
    wanted = normalize_group(group)
    return bool(wanted) and any(normalize_group(e) == wanted for e in (entitlements or []))


def allowed_approval_types(roles: Optional[Iterable[Any]]) -> List[str]:
    """Approval types the given roles own outright, deduplicated."""
    out: List[str] = []
    for role in normalize_roles(roles):
        for approval_type in ROLE_APPROVAL_TYPES.get(role, ()):
            if approval_type not in out:
                out.append(approval_type)
    return out


def sees_unassigned_tasks(roles: Optional[Iterable[Any]]) -> bool:
    return capabilities.has_capability(roles, UNASSIGNED_TASK_CAPABILITY)


def is_unassigned_task(approval: Any) -> bool:
    return (
        getattr(approval, "approval_type", None) == UNASSIGNED_TASK_TYPE
        and not getattr(approval, "assigned_to_email", None)
        and not getattr(approval, "assigned_to_role", None)
    )


def type_visible(approval: Any, roles: Optional[Iterable[Any]]) -> bool:
    """Whether the roles see ``approval`` by its type alone (not by assignment)."""
    if getattr(approval, "approval_type", None) in allowed_approval_types(roles):
        return True
    return is_unassigned_task(approval) and sees_unassigned_tasks(roles)


def approval_visibility_filter(
    email: Optional[str],
    roles: Optional[Iterable[Any]] = None,
    entitlements: Optional[Sequence[str]] = None,
):
    """SQLAlchemy filter for the approvals one user is entitled to see.

    Visible when the approval is assigned to them, delegated to them, addressed
    to a group they belong to, or of a type their role owns. Apply to a query
    that already joins ``ApprovalModel``.

    Group names match case-insensitively, as ``in_group`` does for acting on the
    approval. The exact-match arm stays because SQLite's ``lower()`` folds ASCII only.
    """
    roles = list(roles or [])
    groups = [str(e) for e in (entitlements or []) if e]
    folded = sorted({normalize_group(g) for g in groups if normalize_group(g)})
    arms = [
        ApprovalModel.assigned_to_email == email,
        ApprovalModel.delegated_to_email == email,
        ApprovalModel.assigned_to_role.in_(groups),
        func.lower(func.trim(ApprovalModel.assigned_to_role)).in_(folded),
        ApprovalModel.approval_type.in_(allowed_approval_types(roles)),
    ]
    if sees_unassigned_tasks(roles):
        arms.append(and_(
            ApprovalModel.approval_type == UNASSIGNED_TASK_TYPE,
            or_(ApprovalModel.assigned_to_email.is_(None), ApprovalModel.assigned_to_email == ""),
            or_(ApprovalModel.assigned_to_role.is_(None), ApprovalModel.assigned_to_role == ""),
        ))
    return or_(*arms)
