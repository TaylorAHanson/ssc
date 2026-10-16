"""Environments, managed sheets and header layouts, all derived from settings.

A managed sheet is one of: each environment's UC sheet and WS sheet (named from
``RBAC_UC_SHEET_PATTERN`` / ``RBAC_WS_SHEET_PATTERN`` with the environment's
sheet suffix), plus the single metastore sheet. Settings are read on every call
so an edit in Admin -> Settings applies without a restart.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from app.core.config import settings
from app.db.rbac import PLANE_METASTORE, PLANE_UC, PLANE_WORKSPACE

METASTORE_TARGET = "metastore"

PLANE_LABELS: Dict[str, str] = {
    PLANE_UC: "UC securables",
    PLANE_METASTORE: "UC metastore",
    PLANE_WORKSPACE: "Workspace securables",
}

# The confirmed workbook layouts, used for a sheet that was never imported. An
# import records the sheet's actual header text and order, which then wins.
DEFAULT_COLUMNS: Dict[str, List[Tuple[str, str]]] = {
    PLANE_UC: [
        ("Environment", "environment_value"),
        ("Securable Type", "securable_type"),
        ("Catalog", "catalog"),
        ("SchemaName", "schema_name"),
        ("ObjectName", "object_name"),
        ("Owner", "owner"),
        ("PrincipalType", "principal_type"),
        ("PrincipalName", "principal_name"),
        ("AccessPrivilege", "privilege"),
    ],
    PLANE_WORKSPACE: [
        ("Environment", "environment_value"),
        ("PrincipalType", "principal_type"),
        ("PrincipalName", "principal_name"),
        ("WorkspaceName", "workspace_name"),
        ("SecurableType", "securable_type"),
        ("SecurableName", "object_name"),
        ("SecurablePermission", "privilege"),
    ],
    PLANE_METASTORE: [
        ("Securable Type", "securable_type"),
        ("Catalog", "catalog"),
        ("SchemaName", "schema_name"),
        ("Owner", "owner"),
        ("PrincipalType", "principal_type"),
        ("PrincipalName", "principal_name"),
        ("AccessPrivilege", "privilege"),
    ],
}

# Fields that identify the securable a row is about, per plane.
SECURABLE_FIELDS: Dict[str, Tuple[str, ...]] = {
    PLANE_UC: ("securable_type", "catalog", "schema_name", "object_name"),
    PLANE_METASTORE: ("securable_type", "catalog", "schema_name"),
    PLANE_WORKSPACE: ("workspace_name", "securable_type", "object_name"),
}

# Securable types (workbook spelling, lowercase) and the plane whose sheets hold them.
SECURABLE_TYPES: Dict[str, str] = {
    "catalog": PLANE_UC,
    "schema": PLANE_UC,
    "table": PLANE_UC,
    "view": PLANE_UC,
    "materialized view": PLANE_UC,
    "streaming table": PLANE_UC,
    "volume": PLANE_UC,
    "function": PLANE_UC,
    "model": PLANE_UC,
    "registered model": PLANE_UC,
    "metastore": PLANE_METASTORE,
    "connection": PLANE_METASTORE,
    "external location": PLANE_METASTORE,
    "storage credential": PLANE_METASTORE,
    "service credential": PLANE_METASTORE,
    "share": PLANE_METASTORE,
    "recipient": PLANE_METASTORE,
    "provider": PLANE_METASTORE,
    "clean room": PLANE_METASTORE,
    "workspace": PLANE_WORKSPACE,
    "workspace entitlement": PLANE_WORKSPACE,
    "directory": PLANE_WORKSPACE,
    "folder": PLANE_WORKSPACE,
    "notebook": PLANE_WORKSPACE,
    "cluster": PLANE_WORKSPACE,
    "cluster policy": PLANE_WORKSPACE,
    "instance pool": PLANE_WORKSPACE,
    "job": PLANE_WORKSPACE,
    "pipeline": PLANE_WORKSPACE,
    "sql warehouse": PLANE_WORKSPACE,
    "dashboard": PLANE_WORKSPACE,
    "genie space": PLANE_WORKSPACE,
    "repo": PLANE_WORKSPACE,
    "secret scope": PLANE_WORKSPACE,
    "serving endpoint": PLANE_WORKSPACE,
    "service principal": PLANE_WORKSPACE,
    "app": PLANE_WORKSPACE,
}

# Built-in header spellings, matched after normalizing (lowercase, no spaces,
# underscores or hyphens). Admin aliases (RBAC_HEADER_ALIASES) are added on top.
_BUILTIN_HEADER_FIELDS: Dict[str, str] = {
    "environment": "environment_value",
    "env": "environment_value",
    "securabletype": "securable_type",
    "catalog": "catalog",
    "catalogname": "catalog",
    "schemaname": "schema_name",
    "schema": "schema_name",
    "objectname": "object_name",
    "securablename": "object_name",
    "workspacename": "workspace_name",
    "workspace": "workspace_name",
    "owner": "owner",
    "principaltype": "principal_type",
    "principalname": "principal_name",
    "principal": "principal_name",
    "accessprivilege": "privilege",
    "privilege": "privilege",
    "privileges": "privilege",
    "securablepermission": "privilege",
    "permission": "privilege",
}


@dataclass(frozen=True)
class EnvConfig:
    key: str
    aliases: Tuple[str, ...]
    sheet_suffix: str
    export_path: str
    self_service: bool
    write_privileges: bool
    service_principals: bool
    two_person_rule: bool


@dataclass(frozen=True)
class SheetSpec:
    name: str
    plane: str
    # None for the metastore sheet, which covers every environment.
    environment: Optional[str]
    # An environment key, or METASTORE_TARGET.
    export_target: str
    # "" = not configured (the sheet isn't exported).
    export_path: str


def _flag(row: dict, key: str, default: bool) -> bool:
    val = row.get(key)
    if val is None or val == "":
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(value: object) -> Tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, (list, tuple)):
        parts = [str(v) for v in value]
    else:
        parts = str(value).split(",")
    return tuple(p.strip() for p in parts if p and p.strip())


def environments() -> List[EnvConfig]:
    out: List[EnvConfig] = []
    for row in settings.RBAC_ENVIRONMENTS or []:
        key = str(row.get("key") or "").strip()
        if not key:
            continue
        out.append(EnvConfig(
            key=key,
            aliases=_split_csv(row.get("aliases")),
            sheet_suffix=str(row.get("sheet_suffix") or key).strip(),
            export_path=str(row.get("export_path") or "").strip(),
            self_service=_flag(row, "self_service", True),
            write_privileges=_flag(row, "write_privileges", True),
            service_principals=_flag(row, "service_principals", False),
            two_person_rule=_flag(row, "two_person_rule", False),
        ))
    return out


def environment(key: Optional[str]) -> Optional[EnvConfig]:
    if not key:
        return None
    for env in environments():
        if env.key == key:
            return env
    return None


def resolve_environment(value: Optional[str]) -> Optional[str]:
    """The environment key for a key, alias or sheet suffix (case-insensitive), else None."""
    if not value:
        return None
    needle = str(value).strip().lower()
    if not needle:
        return None
    for env in environments():
        names = {env.key.lower(), env.sheet_suffix.lower(), *(a.lower() for a in env.aliases)}
        if needle in names:
            return env.key
    return None


def uc_sheet_name(env: EnvConfig) -> str:
    return (settings.RBAC_UC_SHEET_PATTERN or "UC_Securables_RBAC_{suffix}").replace("{suffix}", env.sheet_suffix)


def ws_sheet_name(env: EnvConfig) -> str:
    return (settings.RBAC_WS_SHEET_PATTERN or "WS_Securables_RBAC_{suffix}").replace("{suffix}", env.sheet_suffix)


def metastore_sheet_name() -> str:
    return settings.RBAC_METASTORE_SHEET or "UC_metastore_RBAC"


def managed_sheets() -> List[SheetSpec]:
    """Every managed sheet: UC per environment, WS per environment, then the metastore sheet."""
    envs = environments()
    sheets: List[SheetSpec] = []
    for env in envs:
        sheets.append(SheetSpec(uc_sheet_name(env), PLANE_UC, env.key, env.key, env.export_path))
    for env in envs:
        sheets.append(SheetSpec(ws_sheet_name(env), PLANE_WORKSPACE, env.key, env.key, env.export_path))
    sheets.append(SheetSpec(
        metastore_sheet_name(), PLANE_METASTORE, None, METASTORE_TARGET,
        (settings.RBAC_METASTORE_EXPORT_PATH or "").strip(),
    ))
    return sheets


def sheet_spec(name: Optional[str]) -> Optional[SheetSpec]:
    """The managed sheet with this name (exact match first, then case-insensitive)."""
    if not name:
        return None
    sheets = managed_sheets()
    for spec in sheets:
        if spec.name == name:
            return spec
    lowered = name.strip().lower()
    for spec in sheets:
        if spec.name.lower() == lowered:
            return spec
    return None


def sheet_for(plane: str, environment_key: Optional[str]) -> Optional[SheetSpec]:
    """The sheet that holds ``plane`` rows for an environment (the metastore sheet ignores it)."""
    for spec in managed_sheets():
        if spec.plane != plane:
            continue
        if plane == PLANE_METASTORE or spec.environment == environment_key:
            return spec
    return None


def sheets_for_plane(plane: str) -> List[SheetSpec]:
    return [s for s in managed_sheets() if s.plane == plane]


def export_targets() -> List[Tuple[str, str, str, List[str]]]:
    """``(key, label, path, sheet names)`` per export destination, environments first."""
    sheets = managed_sheets()
    out = []
    for env in environments():
        out.append((env.key, env.key, env.export_path,
                    [s.name for s in sheets if s.export_target == env.key]))
    out.append((METASTORE_TARGET, "Metastore", (settings.RBAC_METASTORE_EXPORT_PATH or "").strip(),
                [s.name for s in sheets if s.export_target == METASTORE_TARGET]))
    return out


def plane_for_securable_type(securable_type: Optional[str]) -> Optional[str]:
    if not securable_type:
        return None
    return SECURABLE_TYPES.get(normalize_securable_type(securable_type))


def normalize_securable_type(value: str) -> str:
    """Workbook spelling: lowercase, single spaces (``SQL_WAREHOUSE`` -> ``sql warehouse``)."""
    return re.sub(r"[\s_]+", " ", (value or "").strip().lower())


def default_columns(plane: str) -> List[Dict[str, Optional[str]]]:
    return [{"header": h, "field": f} for h, f in DEFAULT_COLUMNS.get(plane, [])]


def plane_fields(plane: str) -> Tuple[str, ...]:
    """The canonical fields a plane's sheets carry by default."""
    return tuple(f for _, f in DEFAULT_COLUMNS.get(plane, []))


def _norm_header(header: object) -> str:
    return re.sub(r"[\s_\-]+", "", str(header or "")).lower()


def field_for_header(header: object) -> Optional[str]:
    norm = _norm_header(header)
    if not norm:
        return None
    for alias in settings.RBAC_HEADER_ALIASES or []:
        if _norm_header(alias.get("header")) == norm and alias.get("field"):
            return str(alias["field"])
    return _BUILTIN_HEADER_FIELDS.get(norm)


def map_headers(headers: Sequence[object]) -> List[Optional[str]]:
    """Canonical field per column. A field claimed by an earlier column maps later duplicates to None."""
    seen: set = set()
    out: List[Optional[str]] = []
    for header in headers:
        field = field_for_header(header)
        if field and field not in seen:
            seen.add(field)
            out.append(field)
        else:
            out.append(None)
    return out
