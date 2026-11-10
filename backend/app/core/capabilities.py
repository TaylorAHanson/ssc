"""
App roles and the capabilities each one holds — the single place authorization is declared.

Every backend gate asks "may this user do X?" by capability id
(``user.can("settings.manage")`` or the ``require_capability`` FastAPI dependency)
rather than naming roles inline, so who-can-do-what is written down once here and
rendered as the "What each role can do" grid on Admin -> Settings -> Role Permissions.

Rules:

* **Super Admin passes every check**, including ``User.has_role`` for any role. It is
  the only implicit super-role.
* ``Platform Admin`` is the old name of ``Application Admin``. Stored strings still
  spelling it (role mappings, tool rows, settings) resolve to Application Admin, so a
  stale value never locks anyone out; it no longer passes other roles' checks.
* A capability may also be granted by an admin-editable role list
  (``also_from_setting``), e.g. ``METADATA_EDITOR_ROLES``. Those settings stay the
  source of truth for their extra roles; the registry only notes the link.

The registry is read-only at runtime: changing it is a code change, reviewed like any
other authorization change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

SUPER_ADMIN = "Super Admin"
APPLICATION_ADMIN = "Application Admin"
GOVERNANCE_ADMIN = "Governance Admin"
SECURITY_ADMIN = "Security Admin"
OPS_ADMIN = "Ops Admin"
USER = "User"

# The old name of Application Admin, still accepted wherever a role string is read.
LEGACY_PLATFORM_ADMIN = "Platform Admin"


@dataclass(frozen=True)
class RoleDef:
    key: str
    label: str
    description: str


# Display order; also the persona priority (most significant first).
ROLES: Tuple[RoleDef, ...] = (
    RoleDef("super_admin", SUPER_ADMIN,
            "Can do everything, including deciding who holds each role. Keep it to a small "
            "group of owners and break-glass accounts."),
    RoleDef("application_admin", APPLICATION_ADMIN,
            "Runs the application: settings, the admin hub, the tool registry, workflows and "
            "content, and Application Admin approval steps. Sees role mappings but can't change them."),
    RoleDef("governance_admin", GOVERNANCE_ADMIN,
            "Owns data governance: the Control Tower pages, allowlists, Access Rules, the "
            "Metadata Manager, Workflow Studio and the governance tools in chat."),
    RoleDef("security_admin", SECURITY_ADMIN,
            "Handles security approval steps."),
    RoleDef("ops_admin", OPS_ADMIN,
            "Keeps the platform running: sees every request and delegation, schedules, "
            "OmniGuard runs (and can clean them up) and Metadata Manager syncs, and uses the "
            "platform probe tools."),
    RoleDef("user", USER,
            "Everyone signed in: asks the assistant, files requests and handles approvals "
            "addressed to them."),
)

ROLE_LABELS: Tuple[str, ...] = tuple(r.label for r in ROLES)
PERSONA_PRIORITY: Tuple[str, ...] = ROLE_LABELS[:-1]


def _fold(name: object) -> str:
    return " ".join(str(name or "").strip().lower().replace("_", " ").split())


_CANONICAL: Dict[str, str] = {_fold(label): label for label in ROLE_LABELS}
_CANONICAL[_fold(LEGACY_PLATFORM_ADMIN)] = APPLICATION_ADMIN


def canonical_role(name: object) -> str:
    """The canonical label for a role string, folding case, underscores and old names.

    ``platform_admin``, ``Platform Admin`` and ``application admin`` all return
    ``Application Admin``. An unknown name (a custom role an admin typed) comes back
    trimmed but otherwise as given.
    """
    folded = _fold(name)
    if not folded:
        return ""
    return _CANONICAL.get(folded, str(name).strip())


def role_key(name: object) -> str:
    """Snake-case key for a role (``Application Admin`` -> ``application_admin``)."""
    return _fold(canonical_role(name)).replace(" ", "_")


def canonical_roles(roles: Optional[Iterable[object]]) -> Set[str]:
    """Canonical, case-folded role set used for every comparison."""
    return {_fold(canonical_role(r)) for r in (roles or []) if _fold(r)}


def is_legacy_platform_admin(name: object) -> bool:
    return _fold(name) == _fold(LEGACY_PLATFORM_ADMIN)


def is_super_admin(roles: Optional[Iterable[object]]) -> bool:
    return _fold(SUPER_ADMIN) in canonical_roles(roles)


def has_role(roles: Optional[Iterable[object]], role_name: object) -> bool:
    """Whether ``roles`` include ``role_name``. Super Admin holds every role."""
    held = canonical_roles(roles)
    return _fold(SUPER_ADMIN) in held or _fold(canonical_role(role_name)) in held


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    description: str
    group: str
    # Roles that hold it, besides Super Admin (which holds everything).
    roles: Tuple[str, ...]
    # Name of a ``settings`` role-list attribute whose roles also hold it.
    also_from_setting: Optional[str] = None
    also_from_setting_label: Optional[str] = None
    # Shown under the grid, e.g. an approver-group rule the grid can't express.
    note: Optional[str] = None


S, APP, GOV, SEC, OPS = (
    SUPER_ADMIN, APPLICATION_ADMIN, GOVERNANCE_ADMIN, SECURITY_ADMIN, OPS_ADMIN,
)

GROUP_ADMINISTRATION = "Administration"
GROUP_BUILD = "Build & content"
GROUP_GOVERNANCE = "Governance"
GROUP_METADATA = "Metadata Manager"
GROUP_APPROVALS = "Approvals"
GROUP_OPERATIONS = "Requests & operations"
GROUP_CHAT_TOOLS = "Assistant tools"

GROUP_ORDER: Tuple[str, ...] = (
    GROUP_ADMINISTRATION, GROUP_BUILD, GROUP_GOVERNANCE, GROUP_METADATA,
    GROUP_APPROVALS, GROUP_OPERATIONS, GROUP_CHAT_TOOLS,
)

_CAPABILITIES: Tuple[Capability, ...] = (
    # --- Administration ---------------------------------------------------------------
    Capability("roles.manage", "Manage role mappings",
               "Add, change and remove which groups, users and roles map to each app role.",
               GROUP_ADMINISTRATION, (S,)),
    Capability("roles.view", "View role mappings",
               "See the Role Mappings and Role Permissions pages in Settings.",
               GROUP_ADMINISTRATION, (S, APP)),
    Capability("settings.manage", "Change settings",
               "Edit Admin -> Settings, including branding, features and navigation.",
               GROUP_ADMINISTRATION, (S, APP)),
    Capability("admin.hub", "Open the admin hub",
               "Use the Admin pages (dashboard, settings, roles, reports).",
               GROUP_ADMINISTRATION, (S, APP)),
    Capability("dev.tools", "Use developer tools",
               "Call the developer endpoints (database reset, seeding) where they're enabled.",
               GROUP_ADMINISTRATION, (S, APP)),
    # --- Build & content --------------------------------------------------------------
    Capability("tools.registry.view", "View the tool registry",
               "See every tool, where it's enabled and which roles may use it.",
               GROUP_BUILD, (S, APP, GOV)),
    Capability("tools.registry.manage", "Manage the tool registry",
               "Turn tools on and off, change who may use them, sync local tools and register "
               "MCP sources.",
               GROUP_BUILD, (S, APP)),
    Capability("workflows.author", "Author workflows",
               "Create, edit, test and publish workflows in Workflow Studio, and use the "
               "workflow-authoring tools in chat.",
               GROUP_BUILD, (S, APP, GOV)),
    Capability("context_catalog.manage", "Manage the Context Catalog",
               "Add, edit, review and sync the documents the assistant answers from.",
               GROUP_BUILD, (S, APP, GOV)),
    Capability("training.manage", "Manage training",
               "Edit courses, enrolments and learning paths in Training Studio.",
               GROUP_BUILD, (S, APP, GOV)),
    Capability("training.upload", "Upload training records",
               "Bulk-upload completion records from a file.",
               GROUP_BUILD, (S, APP)),
    Capability("preview_features.manage", "Manage preview features",
               "Add, edit and sync the preview features people can request.",
               GROUP_BUILD, (S, APP, GOV)),
    Capability("feedback.manage", "Review feedback",
               "Read and triage the feedback people send from the account menu.",
               GROUP_BUILD, (S, APP, GOV)),
    Capability("legacy_dashboards.manage", "Manage legacy dashboards",
               "Add, edit, hide and remove the legacy dashboard links.",
               GROUP_BUILD, (S, APP)),
    # --- Governance -------------------------------------------------------------------
    Capability("governance.control_tower", "Run the Control Tower",
               "Data contracts (ODCS) and data products (ODPS), governance digests, target "
               "workspaces, starting OmniGuard runs and taking enforcement actions.",
               GROUP_GOVERNANCE, (S, GOV)),
    Capability("allowlist.manage", "Manage the allowlist",
               "Add, change and remove allowlist exceptions OmniGuard honours.",
               GROUP_GOVERNANCE, (S, GOV)),
    Capability("sentinel.view", "View OmniGuard",
               "Open the OmniGuard page and read its runs and findings.",
               GROUP_GOVERNANCE, (S, GOV, OPS)),
    Capability("access_rules.admin", "Administer Access Rules",
               "Edit every plane's rules, import or revert a workbook and reset the rules.",
               GROUP_GOVERNANCE, (S, GOV),
               note="Members of a plane's approver group can still edit that plane's rules."),
    Capability("access_rules.view", "View Access Rules",
               "Open Access Rules and read every plane's rules.",
               GROUP_GOVERNANCE, (S, GOV),
               also_from_setting="RBAC_VIEWER_ROLES",
               also_from_setting_label="Access Rules viewer roles",
               note="Members of any plane's approver group can also view."),
    # --- Metadata Manager -------------------------------------------------------------
    Capability("metadata.admin", "Administer the Metadata Manager",
               "Edit every object's tags and descriptions and manage the Metadata Manager.",
               GROUP_METADATA, (S,),
               also_from_setting="METADATA_EDITOR_ROLES",
               also_from_setting_label="Metadata editor roles"),
    Capability("metadata.resume_paused_sync", "Resume a paused metadata sync",
               "Restart a metadata sync that stopped itself after too many changes.",
               GROUP_METADATA, (S, GOV)),
    Capability("metadata.runs", "See metadata sync runs",
               "Read the sync history and start a check now.",
               GROUP_METADATA, (S, OPS),
               also_from_setting="METADATA_EDITOR_ROLES",
               also_from_setting_label="Metadata editor roles"),
    # --- Approvals --------------------------------------------------------------------
    Capability("approvals.application", "Act on Application Admin steps",
               "Approve or reject Application Admin approval steps, and mark unassigned manual "
               "tasks done.",
               GROUP_APPROVALS, (S, APP)),
    Capability("approvals.governance", "Act on Governance Admin steps",
               "Approve or reject steps addressed to Governance Admins.",
               GROUP_APPROVALS, (S, GOV)),
    Capability("approvals.security", "Act on Security Admin steps",
               "Approve or reject steps addressed to Security Admins.",
               GROUP_APPROVALS, (S, SEC)),
    Capability("requests.edit_parameters", "Edit & Restart a request",
               "Change a waiting request's inputs and restart it from an Application Admin step.",
               GROUP_APPROVALS, (S, APP)),
    Capability("approvals.break_glass", "Approve any step",
               "Act on any pending approval, whoever it's addressed to.",
               GROUP_APPROVALS, (S,),
               note="Nobody may approve their own request while self-approval is blocked."),
    # --- Requests & operations --------------------------------------------------------
    Capability("requests.view_all", "See everyone's requests",
               "List and open every request, check its status and delete it.",
               GROUP_OPERATIONS, (S, APP, OPS)),
    Capability("delegations.view_all", "See every delegation",
               "List every approval delegation, not just your own, and remove any of them.",
               GROUP_OPERATIONS, (S, APP, OPS)),
    Capability("ops.sentinel_runs", "Clean up OmniGuard runs",
               "Delete a stuck OmniGuard run or purge old ones.",
               GROUP_OPERATIONS, (S, APP, OPS)),
    Capability("ops.health", "See schedules and health",
               "Read job schedules and other platform health information.",
               GROUP_OPERATIONS, (S, APP, OPS)),
    # --- Assistant tools --------------------------------------------------------------
    Capability("platform.probe", "Use the platform probe tools",
               "Run the LMWS probe tools and the URL reachability check in chat. Both make "
               "requests from the app's own network position.",
               GROUP_CHAT_TOOLS, (S, APP, OPS)),
    Capability("governance.tools", "Use governance tools",
               "Draft data contracts (ODCS) and data products (ODPS) in chat.",
               GROUP_CHAT_TOOLS, (S, GOV)),
)

CAPABILITIES: Dict[str, Capability] = {c.id: c for c in _CAPABILITIES}


def get_capability(capability_id: str) -> Capability:
    try:
        return CAPABILITIES[capability_id]
    except KeyError:
        raise ValueError(f"Unknown capability '{capability_id}'") from None


def setting_roles(capability_id: str) -> List[str]:
    """Current roles from the capability's ``also_from_setting`` list (canonical labels)."""
    cap = get_capability(capability_id)
    if not cap.also_from_setting:
        return []
    from app.core.config import settings

    raw = getattr(settings, cap.also_from_setting, None) or []
    if isinstance(raw, str):
        raw = raw.split(",")
    out: List[str] = []
    for r in raw:
        label = canonical_role(r)
        if label and label not in out:
            out.append(label)
    return out


def roles_for(capability_id: str, *, include_setting: bool = True) -> List[str]:
    """Roles holding a capability besides Super Admin, in role order then setting order."""
    cap = get_capability(capability_id)
    out = [r for r in cap.roles if r != SUPER_ADMIN]
    if include_setting:
        out += [r for r in setting_roles(capability_id) if r not in out and r != SUPER_ADMIN]
    return out


def has_capability(roles: Optional[Iterable[object]], capability_id: str) -> bool:
    cap = get_capability(capability_id)
    held = canonical_roles(roles)
    if _fold(SUPER_ADMIN) in held:
        return True
    if any(_fold(r) in held for r in cap.roles):
        return True
    return bool(cap.also_from_setting) and any(_fold(r) in held for r in setting_roles(capability_id))


def capabilities_for(roles: Optional[Iterable[object]]) -> List[str]:
    """Every capability id the roles hold, in registry order."""
    roles = list(roles or [])
    return [c.id for c in _CAPABILITIES if has_capability(roles, c.id)]


def all_capabilities() -> Tuple[Capability, ...]:
    return _CAPABILITIES


def persona_for(roles: Optional[Iterable[object]]) -> str:
    """The single most significant role, for display (Super > Application > ... > User)."""
    held = canonical_roles(roles)
    for label in PERSONA_PRIORITY:
        if _fold(label) in held:
            return label
    return USER


def denied_detail(capability_id: str) -> str:
    """A 403 message naming what was needed and who holds it."""
    cap = get_capability(capability_id)
    holders = [SUPER_ADMIN] + roles_for(capability_id)
    return f"You need the '{cap.label}' permission, held by: {', '.join(holders)}."
