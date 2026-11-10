"""
The Databricks workspaces a person can use, read from the Access Rules.

A workspace counts when one of the person's groups holds a ``workspace`` or
``workspace entitlement`` rule on it. Built-in groups everyone is in
(``RBAC_SYSTEM_GROUPS``) never count, as in ``find_access``. Each workspace's
address comes from the cached account workspace list, falling back to the
configured target workspaces; a workspace with neither is still listed, without
links.
"""
import logging
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

WORKSPACE_SECURABLE_TYPES = ("workspace", "workspace entitlement")
_RULE_LIMIT = 1000


def _fold(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def user_groups(entitlements: Iterable[str], email: Optional[str]) -> List[str]:
    """The person's groups: their entitlements minus their own email and built-in groups."""
    me = _fold(email)
    system = {_fold(g) for g in (settings.RBAC_SYSTEM_GROUPS or []) if g}
    out: List[str] = []
    for group in entitlements or []:
        folded = _fold(group)
        if folded and folded != me and folded not in system and group not in out:
            out.append(str(group).strip())
    return out


def _normalize_host(raw: Optional[str]) -> str:
    url = (raw or "").strip().rstrip("/")
    if not url:
        return ""
    return url if url.startswith(("http://", "https://")) else f"https://{url}"


def _hosts_by_name(db: Session) -> Dict[str, str]:
    """Workspace name -> address, from the target workspaces then the account cache (which wins)."""
    hosts: Dict[str, str] = {}
    try:
        from app.core.workspaces import get_target_workspace_info

        for ws in get_target_workspace_info(enrich=False):
            if ws.name and ws.host:
                hosts[_fold(ws.name)] = _normalize_host(ws.host)
    except Exception as e:  # noqa: BLE001 - a missing address only drops the links
        logger.warning("Could not read the target workspaces for workspace links: %s", e)
    try:
        from app.db.workspace_cache import CachedWorkspaceModel

        for name, url in db.query(CachedWorkspaceModel.workspace_name, CachedWorkspaceModel.workspace_url).all():
            if name and url:
                hosts[_fold(name)] = _normalize_host(url)
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read the cached workspace list for workspace links: %s", e)
    return hosts


def workspaces_for(db: Session, groups: List[str]) -> List[Dict[str, Any]]:
    """One entry per workspace a group in ``groups`` holds a rule on, sorted by name."""
    if not groups:
        return []
    from app.services.rbac import ledger

    found: Dict[str, Dict[str, Any]] = {}
    for securable_type in WORKSPACE_SECURABLE_TYPES:
        result = ledger.find_access(
            db, securable_type=securable_type, principals_in=groups, limit=_RULE_LIMIT,
        )
        if result.truncated:
            logger.warning("Workspace list for %d group(s) hit the %d-rule cap", len(groups), _RULE_LIMIT)
        for match in result.matches:
            name = (match.workspace_name or "").strip()
            if not name:
                continue
            entry = found.setdefault(_fold(name), {
                "name": name, "environments": [], "groups": [], "privileges": [],
            })
            for key, values in (
                ("environments", [match.environment] if match.environment else []),
                ("groups", [match.principal_name]),
                ("privileges", match.privileges),
            ):
                for value in values:
                    if value and value not in entry[key]:
                        entry[key].append(value)

    hosts = _hosts_by_name(db) if found else {}
    out: List[Dict[str, Any]] = []
    for key, entry in sorted(found.items()):
        host = hosts.get(key, "")
        out.append({
            **entry,
            "is_admin": any(_fold(p) == "admin" for p in entry["privileges"]),
            "host": host,
            "lakehouse_url": host,
            "genie_one_url": f"{host}/one" if host else "",
        })
    return out
