"""The single policy module every Access Rules write path calls (ADR 0005 D7).

It judges what a change *adds* (new atoms and changed values), never atoms a
change leaves alone. Removing atoms is always allowed, except on system groups.
All rules come from settings; the privilege vocabulary is an in-code registry.

Severity depends on the write path:

- hard rules deny on ``request`` and ``admin_edit``;
- self-service rules deny on ``request`` and warn on ``admin_edit``;
- ``import`` is warn-only, so production data always loads.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from app.core.config import settings
from app.db.rbac import PLANE_METASTORE, PLANE_UC, PLANE_WORKSPACE
from app.models.rbac import CANONICAL_FIELDS, Finding, RuleRow, Severity, WritePath
from app.services.rbac import layout
from app.services.rbac.cells import (
    atoms,
    cell_key,
    principal_type_key,
    securable_key,
    split_cell,
)

logger = logging.getLogger(__name__)

_UC_DATA = frozenset({"ALL_PRIVILEGES", "APPLY_TAG", "MANAGE"})
_CONTAINER = _UC_DATA | {
    "CREATE_FUNCTION", "CREATE_MATERIALIZED_VIEW", "CREATE_MODEL", "CREATE_TABLE", "CREATE_VOLUME",
    "EXECUTE", "EXTERNAL_USE_SCHEMA", "MODIFY", "READ_VOLUME", "REFRESH", "SELECT", "USE_SCHEMA",
    "WRITE_VOLUME",
}
_WS_FILE_ACL = frozenset({"CAN_READ", "CAN_RUN", "CAN_EDIT", "CAN_MANAGE"})

# securable_type (workbook spelling) -> privileges valid on it, following the
# Databricks Unity Catalog privilege model and workspace ACLs. Workspace
# entitlements are lowercase, as the workbook writes them.
PRIVILEGES: Dict[str, frozenset] = {
    # UC securables
    "catalog": frozenset(_CONTAINER | {"BROWSE", "CREATE_SCHEMA", "USE_CATALOG"}),
    "schema": frozenset(_CONTAINER),
    "table": frozenset(_UC_DATA | {"MODIFY", "SELECT"}),
    # MODIFY on a view isn't in the UC model, but the workbook grants it.
    "view": frozenset(_UC_DATA | {"MODIFY", "SELECT"}),
    "materialized view": frozenset(_UC_DATA | {"REFRESH", "SELECT"}),
    "streaming table": frozenset(_UC_DATA | {"REFRESH", "SELECT"}),
    "volume": frozenset(_UC_DATA | {"READ_VOLUME", "WRITE_VOLUME"}),
    "function": frozenset({"ALL_PRIVILEGES", "EXECUTE", "MANAGE"}),
    "model": frozenset(_UC_DATA | {"EXECUTE"}),
    "registered model": frozenset(_UC_DATA | {"EXECUTE"}),
    # UC metastore-level securables
    "metastore": frozenset({
        "CREATE_CATALOG", "CREATE_CLEAN_ROOM", "CREATE_CONNECTION", "CREATE_EXTERNAL_LOCATION",
        "CREATE_PROVIDER", "CREATE_RECIPIENT", "CREATE_SERVICE_CREDENTIAL", "CREATE_SHARE",
        "CREATE_STORAGE_CREDENTIAL", "MANAGE_ALLOWLIST", "SET_SHARE_PERMISSION",
        "USE_MARKETPLACE_ASSETS", "USE_PROVIDER", "USE_RECIPIENT", "USE_SHARE",
    }),
    "connection": frozenset({"ALL_PRIVILEGES", "CREATE_FOREIGN_CATALOG", "MANAGE", "USE_CONNECTION"}),
    "external location": frozenset({
        "ALL_PRIVILEGES", "BROWSE", "CREATE_EXTERNAL_TABLE", "CREATE_EXTERNAL_VOLUME",
        "CREATE_FOREIGN_SECURABLE", "CREATE_MANAGED_STORAGE", "EXTERNAL_USE_LOCATION", "MANAGE",
        "READ_FILES", "WRITE_FILES",
    }),
    "storage credential": frozenset({
        "ALL_PRIVILEGES", "CREATE_EXTERNAL_LOCATION", "CREATE_EXTERNAL_TABLE", "MANAGE",
        "READ_FILES", "WRITE_FILES",
    }),
    "service credential": frozenset({"ACCESS", "ALL_PRIVILEGES", "MANAGE"}),
    # Shares are granted to recipients; providers and recipients take no grants
    # (their USE_* privileges are on the metastore).
    "share": frozenset({"SELECT"}),
    "recipient": frozenset(),
    "provider": frozenset(),
    # Workspace securables
    "workspace": frozenset({"USER", "ADMIN"}),
    "workspace entitlement": frozenset({
        "workspace_access", "databricks_sql_access", "allow_cluster_create",
        "allow_instance_pool_create", "workspace_consume",
    }),
    "cluster": frozenset({"CAN_ATTACH_TO", "CAN_RESTART", "CAN_MANAGE"}),
    "cluster policy": frozenset({"CAN_USE"}),
    "instance pool": frozenset({"CAN_ATTACH_TO", "CAN_MANAGE"}),
    "job": frozenset({"CAN_VIEW", "CAN_MANAGE_RUN", "IS_OWNER", "CAN_MANAGE"}),
    "pipeline": frozenset({"CAN_VIEW", "CAN_RUN", "CAN_MANAGE", "IS_OWNER"}),
    "sql warehouse": frozenset({"CAN_VIEW", "CAN_MONITOR", "CAN_USE", "CAN_MANAGE", "IS_OWNER"}),
    "notebook": _WS_FILE_ACL,
    "directory": _WS_FILE_ACL,
    "folder": _WS_FILE_ACL,
    "repo": _WS_FILE_ACL,
    "dashboard": _WS_FILE_ACL,
    "genie space": frozenset({"CAN_VIEW", "CAN_RUN", "CAN_EDIT", "CAN_MANAGE"}),
    "secret scope": frozenset({"READ", "WRITE", "MANAGE"}),
    "serving endpoint": frozenset({"CAN_VIEW", "CAN_QUERY", "CAN_MANAGE"}),
    "service principal": frozenset({
        "roles/servicePrincipal.user", "roles/servicePrincipal.manager", "CAN_USE", "CAN_MANAGE",
    }),
    "app": frozenset({"CAN_USE", "CAN_MANAGE"}),
}

# Deprecated tokens -> replacement hint. ``ALL PRIVILEGES`` with a space is just
# ALL_PRIVILEGES (see normalize_privilege), not a deprecated token.
DEPRECATED: Dict[str, str] = {
    "USAGE": "Use USE_CATALOG on a catalog or USE_SCHEMA on a schema.",
    "READ_METADATA": "Use BROWSE.",
    "CREATE": "Use a specific privilege such as CREATE_SCHEMA or CREATE_TABLE.",
    "CREATE_NAMED_FUNCTION": "Use CREATE_FUNCTION.",
}

# Privileges that write data or change objects (blocked where an environment
# turns write privileges off). Any CREATE_* token also counts (is_write_privilege).
WRITE_PRIVILEGES: frozenset = frozenset(
    {
        "MODIFY", "WRITE_VOLUME", "WRITE_FILES", "REFRESH", "APPLY_TAG", "MANAGE", "ALL_PRIVILEGES",
        "ADMIN", "CAN_MANAGE", "CAN_EDIT", "CAN_MANAGE_RUN", "IS_OWNER",
        # Secret scope write and the service principal manager role.
        "WRITE", "roles/servicePrincipal.manager",
    }
    | {p for privs in PRIVILEGES.values() for p in privs if p.upper().startswith("CREATE_")}
)

# Granting these on a whole catalog reaches every schema in it.
BROAD_CATALOG_PRIVILEGES = frozenset({"SELECT", "MODIFY", "READ_VOLUME", "WRITE_VOLUME", "EXECUTE"})

_HARD: Dict[str, Severity] = {"request": "deny", "admin_edit": "deny", "import": "warn"}
_SELF_SERVICE: Dict[str, Severity] = {"request": "deny", "admin_edit": "warn", "import": "warn"}
_REQUEST_ONLY: Dict[str, Severity] = {"request": "deny", "admin_edit": "warn", "import": "warn"}
_ADMIN_EDIT_ONLY: Dict[str, Severity] = {"request": "warn", "admin_edit": "deny", "import": "warn"}
_WARN: Dict[str, Severity] = {"request": "warn", "admin_edit": "warn", "import": "warn"}

_REQUIRED_FIELDS = ("securable_type", "principal_name", "privilege")
_PACKED_FIELDS = ("principal_name", "privilege")
_KNOWN_PRINCIPAL_TYPES = ("group", "service principal", "user")


def normalize_privilege(token: Optional[str]) -> str:
    """Comparison key for a privilege: trimmed, uppercase, spaces -> underscores."""
    return re.sub(r"\s+", "_", (token or "").strip()).upper()


def _index(values) -> Dict[str, str]:
    return {normalize_privilege(v): v for v in values}


def _index_by_plane() -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for kind, privs in sorted(PRIVILEGES.items()):
        out.setdefault(layout.SECURABLE_TYPES.get(kind, ""), {}).update(_index(privs))
    return out


_BY_TYPE: Dict[str, Dict[str, str]] = {kind: _index(privs) for kind, privs in PRIVILEGES.items()}
_BY_PLANE: Dict[str, Dict[str, str]] = _index_by_plane()
_WRITE_KEYS = frozenset(normalize_privilege(p) for p in WRITE_PRIVILEGES)


def canonical_privilege(securable_type: Optional[str], token: str) -> str:
    """The registry spelling of ``token`` on ``securable_type`` (``select`` -> ``SELECT``,
    ``Workspace_Access`` -> ``workspace_access``).

    Falls back to any spelling the registry knows on the type's plane, then to
    uppercase-with-underscores on UC / metastore types, else the trimmed token.
    """
    raw = (token or "").strip()
    key = normalize_privilege(raw)
    kind = layout.normalize_securable_type(securable_type or "")
    if key in _BY_TYPE.get(kind, {}):
        return _BY_TYPE[kind][key]
    plane = layout.SECURABLE_TYPES.get(kind)
    if plane and key in _BY_PLANE.get(plane, {}):
        return _BY_PLANE[plane][key]
    if plane in (PLANE_UC, PLANE_METASTORE):
        return key
    return raw


def is_write_privilege(token: Optional[str]) -> bool:
    key = normalize_privilege(token)
    return key in _WRITE_KEYS or key.startswith("CREATE_")


def is_valid_privilege(securable_type: Optional[str], token: Optional[str]) -> Optional[bool]:
    """Whether the registry allows ``token`` on the type; None when the type isn't in the registry."""
    kind = layout.normalize_securable_type(securable_type or "")
    if kind not in _BY_TYPE:
        return None
    return normalize_privilege(token) in _BY_TYPE[kind]


# --- settings -----------------------------------------------------------------


def _as_list(value: object) -> List[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        parts = [str(v) for v in value]
    else:
        parts = str(value).split(",")
    return [p.strip() for p in parts if p and p.strip()]


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass
class _NamingRule:
    field: str
    pattern: str
    securable_types: Set[str]
    exceptions: Set[str]
    severity: Dict[str, Severity]
    message: str


def _naming_rules() -> List[_NamingRule]:
    out: List[_NamingRule] = []
    for raw in settings.RBAC_NAMING_RULES or []:
        if not isinstance(raw, dict):
            continue
        pattern = str(raw.get("pattern") or "")
        name = str(raw.get("field") or "").strip()
        if not pattern or not name:
            continue
        fld = name if name in CANONICAL_FIELDS else layout.field_for_header(name)
        if fld not in CANONICAL_FIELDS:
            logger.warning("Access Rules naming rule skipped: unknown field %r", name)
            continue
        out.append(_NamingRule(
            field=fld,
            pattern=pattern,
            securable_types={layout.normalize_securable_type(t) for t in _as_list(raw.get("securable_types"))},
            exceptions={cell_key(e) for e in _as_list(raw.get("exceptions"))},
            severity=_HARD if str(raw.get("severity") or "").strip().lower() == "deny" else _WARN,
            message=str(raw.get("message") or "").strip(),
        ))
    return out


# --- evaluation ---------------------------------------------------------------


@dataclass
class _Change:
    index: int
    before: Optional[RuleRow]
    after: Optional[RuleRow]
    row: RuleRow
    plane: str
    env: Optional[layout.EnvConfig]
    added: List[Tuple[str, str]] = field(default_factory=list)
    removed: List[Tuple[str, str]] = field(default_factory=list)
    added_principals: List[str] = field(default_factory=list)
    removed_principals: List[str] = field(default_factory=list)
    new_principal_names: List[str] = field(default_factory=list)
    new_privileges: List[str] = field(default_factory=list)
    changed_fields: List[str] = field(default_factory=list)

    @property
    def rule_id(self) -> Optional[str]:
        return (self.before.id if self.before else None) or (self.after.id if self.after else None)


def _atom_list(row: Optional[RuleRow]) -> Dict[Tuple[str, str], Tuple[str, str]]:
    out: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for atom in atoms(row) if row else []:
        out.setdefault((cell_key(atom.principal), normalize_privilege(atom.privilege)), (atom.principal, atom.privilege))
    return out


def _unique(values: Sequence[str], exclude: Sequence[str] = ()) -> List[str]:
    seen = {cell_key(v) for v in exclude}
    out: List[str] = []
    for value in values:
        if cell_key(value) not in seen:
            seen.add(cell_key(value))
            out.append(value)
    return out


def _same_identity(before: RuleRow, after: RuleRow) -> bool:
    return (
        before.plane == after.plane
        and securable_key(before, after.plane) == securable_key(after, after.plane)
        and principal_type_key(before.principal_type) == principal_type_key(after.principal_type)
    )


def _describe(index: int, before: Optional[RuleRow], after: Optional[RuleRow]) -> Optional[_Change]:
    row = after or before
    if row is None:
        return None
    change = _Change(index=index, before=before, after=after, row=row, plane=row.plane,
                     env=layout.environment(row.environment))
    old = _atom_list(before)
    new = _atom_list(after)
    same = before is not None and after is not None and _same_identity(before, after)
    change.added = [v for k, v in new.items() if not same or k not in old]
    change.removed = [v for k, v in old.items() if not same or k not in new]

    before_principals = split_cell(before.principal_name) if before else []
    after_principals = split_cell(after.principal_name) if after else []
    change.new_principal_names = _unique(after_principals, exclude=before_principals)
    change.added_principals = _unique([p for p, _ in change.added] + change.new_principal_names)
    change.removed_principals = _unique([p for p, _ in change.removed]
                                        + _unique(before_principals, exclude=after_principals))
    before_privileges = {normalize_privilege(p) for p in split_cell(before.privilege)} if before else set()
    change.new_privileges = [p for p in _unique(split_cell(after.privilege) if after else [])
                             if normalize_privilege(p) not in before_privileges]

    if after is not None:
        for fld in CANONICAL_FIELDS:
            if fld in _PACKED_FIELDS:
                continue
            value = (getattr(after, fld) or "").strip()
            if value and (before is None or (getattr(before, fld) or "").strip() != value):
                change.changed_fields.append(fld)
    return change


def _label(plane: str, fld: str) -> str:
    for header, mapped in layout.DEFAULT_COLUMNS.get(plane, []):
        if mapped == fld:
            return header
    return fld


def _is_empty(row: RuleRow, fld: str) -> bool:
    value = getattr(row, fld) or ""
    return not split_cell(value) if fld in _PACKED_FIELDS else not value.strip()


class _Evaluator:
    def __init__(self, path: WritePath):
        self.path = path
        self.findings: List[Finding] = []
        self._seen: Set[Tuple] = set()
        self.hard_denied = {normalize_privilege(p) for p in _as_list(settings.RBAC_HARD_DENIED_PRIVILEGES)}
        self.system_groups = {cell_key(g) for g in _as_list(settings.RBAC_SYSTEM_GROUPS)}
        self.naming_rules = _naming_rules()
        self._compiled: Dict[str, Optional[re.Pattern]] = {}

    def severity(self, table: Dict[str, Severity]) -> Severity:
        return "warn" if self.path == "import" else table.get(self.path, "warn")

    def add(self, change: Optional[_Change], code: str, table: Dict[str, Severity], message: str, **kw) -> None:
        key = (code, change.index if change else None, cell_key(kw.get("principal")),
               normalize_privilege(kw.get("privilege")), kw.get("field"), message)
        if key in self._seen:
            return
        self._seen.add(key)
        self.findings.append(Finding(
            severity=self.severity(table),
            code=code,
            message=message,
            sheet=change.row.sheet if change else None,
            rule_id=change.rule_id if change else None,
            change_index=change.index if change else None,
            **kw,
        ))

    def compile(self, pattern: str) -> Optional[re.Pattern]:
        if pattern not in self._compiled:
            try:
                self._compiled[pattern] = re.compile(pattern)
            except re.error as exc:
                logger.warning("Access Rules naming rule skipped: invalid pattern %r (%s)", pattern, exc)
                self._compiled[pattern] = None
        return self._compiled[pattern]

    def check(self, change: _Change) -> None:
        after = change.after
        if after is not None:
            self.check_cells(change, after)
            self.check_principals(change, after)
        for principal in change.removed_principals:
            if cell_key(principal) in self.system_groups:
                self.add(change, "system_group", _HARD,
                         f"{principal} is a system group, so its access can't be changed here.",
                         principal=principal, field="principal_name")
        if after is not None:
            self.check_privileges(change, after)
            self.check_environment(change)
            self.check_naming(change, after)

    def check_cells(self, change: _Change, after: RuleRow) -> None:
        before = change.before
        for fld in _REQUIRED_FIELDS:
            # An update only answers for a cell it empties; rows that already had a
            # gap don't block unrelated edits.
            if _is_empty(after, fld) and (before is None or not _is_empty(before, fld)):
                self.add(change, "missing_cells", _HARD,
                         f"{_label(change.plane, fld)} is empty. Fill it in, or delete the row.", field=fld)
        kind = layout.normalize_securable_type(after.securable_type)
        if "securable_type" in change.changed_fields and kind not in layout.SECURABLE_TYPES:
            self.add(change, "unknown_securable_type", _SELF_SERVICE,
                     f'"{after.securable_type.strip()}" isn\'t a known securable type.', field="securable_type")
        elif ("securable_type" in change.changed_fields and change.plane
              and layout.SECURABLE_TYPES.get(kind, change.plane) != change.plane):
            self.add(change, "securable_type_wrong_sheet", _HARD,
                     f"A {kind} rule belongs on a {layout.PLANE_LABELS[layout.SECURABLE_TYPES[kind]]} sheet, "
                     f"not this {layout.PLANE_LABELS.get(change.plane, change.plane)} one.",
                     field="securable_type")

    def check_principals(self, change: _Change, after: RuleRow) -> None:
        ptype = principal_type_key(after.principal_type)
        for principal in change.added_principals:
            if cell_key(principal) in self.system_groups:
                self.add(change, "system_group", _HARD,
                         f"{principal} is a system group, so its access can't be changed here.",
                         principal=principal, field="principal_name")
            if ptype == "user":
                self.add(change, "principal_type_not_allowed", _HARD,
                         f"{principal} is a user. Grant access to a group instead.",
                         principal=principal, field="principal_type")
            elif ptype == "service principal":
                if change.env is None or not change.env.service_principals:
                    where = f"in {change.env.key}" if change.env else "without an environment that allows them"
                    self.add(change, "principal_type_not_allowed", _SELF_SERVICE,
                             f"Service principals can't be granted access {where}. Use a group.",
                             principal=principal, field="principal_type")
            elif ptype not in _KNOWN_PRINCIPAL_TYPES:
                shown = f'"{after.principal_type.strip()}"' if after.principal_type.strip() else "empty"
                self.add(change, "principal_type_not_allowed", _HARD,
                         f"PrincipalType is {shown}. Use group or service principal.",
                         principal=principal, field="principal_type")

    def check_privileges(self, change: _Change, after: RuleRow) -> None:
        kind = layout.normalize_securable_type(after.securable_type)
        env = change.env
        for principal, privilege in change.added:
            key = normalize_privilege(privilege)
            kw = {"principal": principal, "privilege": privilege, "field": "privilege"}
            if change.plane != PLANE_WORKSPACE and key in DEPRECATED:
                self.add(change, "deprecated_privilege", _HARD,
                         f"{privilege} is deprecated. {DEPRECATED[key]}", **kw)
                continue
            if key in self.hard_denied:
                self.add(change, "hard_denied_privilege", _HARD,
                         f"{privilege} can't be granted through Access Rules. Remove it.", **kw)
            if kind in _BY_TYPE and key not in _BY_TYPE[kind]:
                valid = ", ".join(sorted(PRIVILEGES[kind], key=str.upper))
                hint = f" Valid privileges: {valid}." if valid else " It takes no grants."
                self.add(change, "unknown_privilege", _SELF_SERVICE,
                         f"{privilege} isn't a valid privilege on a {kind}.{hint}", **kw)
            if env is not None and not env.write_privileges and is_write_privilege(privilege):
                self.add(change, "write_privilege_disabled", _SELF_SERVICE,
                         f"{privilege} is a write privilege, and write privileges are turned off in {env.key}.", **kw)
            if change.plane == PLANE_UC and kind == "catalog" and key in BROAD_CATALOG_PRIVILEGES:
                self.add(change, "broad_grant", _WARN,
                         f"{privilege} on catalog {after.catalog.strip()} reaches every schema in it. "
                         "A schema-level grant is usually enough.", **kw)

    def check_environment(self, change: _Change) -> None:
        env = change.env
        if env is None or env.self_service:
            return
        message = f"Self-service changes are turned off in {env.key}."
        if change.added_principals:
            for principal in change.added_principals:
                self.add(change, "self_service_disabled", _SELF_SERVICE, message, principal=principal)
        elif change.added or change.changed_fields:
            self.add(change, "self_service_disabled", _SELF_SERVICE, message)

    def check_naming(self, change: _Change, after: RuleRow) -> None:
        kind = layout.normalize_securable_type(after.securable_type)
        env_key = change.row.environment or ""
        for rule in self.naming_rules:
            if rule.securable_types and kind not in rule.securable_types:
                continue
            if rule.field == "principal_name":
                values = change.new_principal_names
            elif rule.field == "privilege":
                values = change.new_privileges
            elif rule.field in change.changed_fields:
                values = [(getattr(after, rule.field) or "").strip()]
            else:
                continue
            compiled = self.compile(rule.pattern.replace("{env}", re.escape(env_key)))
            if compiled is None:
                continue
            for value in values:
                if not value or cell_key(value) in rule.exceptions or compiled.fullmatch(value):
                    continue
                detail = rule.message.replace("{env}", env_key) if rule.message else "doesn't match the naming rule."
                kw = {"field": rule.field}
                if rule.field == "principal_name":
                    kw["principal"] = value
                elif rule.field == "privilege":
                    kw["privilege"] = value
                self.add(change, "naming_rule", rule.severity,
                         f'{_label(change.plane, rule.field)} "{value}": {detail}', **kw)


def evaluate(
    changes: Sequence[Tuple[Optional[RuleRow], Optional[RuleRow]]],
    *,
    path: WritePath,
    justification: Optional[str] = None,
    reason: Optional[str] = None,
) -> List[Finding]:
    """Findings for a batch of ``(before, after)`` row pairs.

    ``before`` is None for a new row and ``after`` is None for a deleted row.
    Each finding's ``change_index`` is the pair's index in ``changes``. On the
    ``import`` path every finding is downgraded to ``warn``. ``justification``
    (requests) and ``reason`` (direct edits) are checked against their minimum
    lengths when given.

    Atom findings (privilege and principal rules) carry ``principal`` and, for
    privilege rules, ``privilege``, so a caller can map a finding on a merged
    row diff back to the request items that added that atom. Findings are in
    change order; justification and reason findings come last with no
    ``change_index``.
    """
    evaluator = _Evaluator(path)
    for index, (before, after) in enumerate(changes):
        change = _describe(index, before, after)
        if change is not None:
            evaluator.check(change)

    if justification is not None:
        minimum = _as_int(settings.RBAC_MIN_JUSTIFICATION_LENGTH, 0)
        if len(justification.strip()) < minimum:
            evaluator.add(None, "justification_too_short", _REQUEST_ONLY,
                          f"The justification is too short. Explain why the access is needed "
                          f"in at least {minimum} characters.", field="justification")
    if reason is not None:
        minimum = _as_int(settings.RBAC_MIN_REASON_LENGTH, 0)
        if len(reason.strip()) < minimum:
            evaluator.add(None, "reason_too_short", _ADMIN_EDIT_ONLY,
                          f"The reason is too short. Say why you're making this change "
                          f"in at least {minimum} characters.", field="reason")
    return evaluator.findings


def is_blocked(findings: Sequence[Finding]) -> bool:
    return any(f.severity == "deny" for f in findings)
