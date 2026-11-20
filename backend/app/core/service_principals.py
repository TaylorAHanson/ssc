"""
Named service principals: one list of the SPs an installation uses.

Admins list each SP once under Admin -> Settings -> Service Principals (a name,
the client-id / client-secret KEY names in the install-wide secret scope, and
what it's for), and a feature picks one by name instead of carrying its own
key names. Secret values and grants stay in Databricks; the app's own SP needs
READ on the scope (``TARGET_WORKSPACE_SP_SECRET_SCOPE``), as for target workspaces.

Used today by the Access Rules exports (``RBAC_EXPORT_SERVICE_PRINCIPAL``), so a
dedicated SP can hold write access to the export volumes instead of the app's.
:func:`usage` lists which identity each part of the app uses, for the settings page.

A named SP never falls back to the app's SP: if its secrets can't be read the
caller gets :class:`ServicePrincipalError`, so a misconfiguration fails loudly
instead of quietly writing as a different identity.
"""
from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

SETTINGS_PATH = "Admin → Settings → Service Principals"
APP_SP_LABEL = "The app's service principal"

# Successful secret reads only, for a few minutes, so a fixed key name or a new
# scope grant is picked up without a restart (unlike target-workspace secrets).
_SECRET_TTL_SECONDS = 300
_secret_cache: Dict[Tuple[str, str], Tuple[float, str]] = {}
_cache_lock = threading.Lock()


class ServicePrincipalError(Exception):
    """A named service principal is missing, incomplete, or its secrets can't be read."""


@dataclass(frozen=True)
class NamedServicePrincipal:
    name: str
    client_id_key: str
    client_secret_key: str
    purpose: str = ""


def _scope() -> str:
    return (getattr(settings, "TARGET_WORKSPACE_SP_SECRET_SCOPE", "") or "").strip()


def configured() -> List[NamedServicePrincipal]:
    out: List[NamedServicePrincipal] = []
    for row in getattr(settings, "SERVICE_PRINCIPALS", None) or []:
        if not isinstance(row, dict) or not str(row.get("name") or "").strip():
            continue
        out.append(NamedServicePrincipal(
            name=str(row["name"]).strip(),
            client_id_key=str(row.get("client_id_key") or "").strip(),
            client_secret_key=str(row.get("client_secret_key") or "").strip(),
            purpose=str(row.get("purpose") or "").strip(),
        ))
    return out


def names() -> List[str]:
    return [sp.name for sp in configured()]


def get(name: str) -> Optional[NamedServicePrincipal]:
    wanted = (name or "").strip()
    return next((sp for sp in configured() if sp.name == wanted), None)


def _read_secret(scope: str, key: str) -> str:
    now = time.monotonic()
    with _cache_lock:
        hit = _secret_cache.get((scope, key))
        if hit and now - hit[0] < _SECRET_TTL_SECONDS:
            return hit[1]
    from databricks.sdk import WorkspaceClient

    try:
        secret = WorkspaceClient().secrets.get_secret(scope=scope, key=key)
        value = base64.b64decode(secret.value or "").decode("utf-8").strip() if secret else ""
    except Exception as exc:  # noqa: BLE001 - reported to the caller with the fix
        raise ServicePrincipalError(
            f'Couldn\'t read secret "{key}" from scope "{scope}". Check the key name exists in the scope '
            f"and that the app's own service principal has READ on it. ({exc})"
        ) from exc
    if not value:
        raise ServicePrincipalError(f'Secret "{key}" in scope "{scope}" is empty.')
    with _cache_lock:
        _secret_cache[(scope, key)] = (now, value)
    return value


def credentials(name: str) -> Tuple[str, str]:
    """``(client_id, client_secret)`` for a named SP, or :class:`ServicePrincipalError` saying what to fix."""
    sp = get(name)
    if sp is None:
        raise ServicePrincipalError(
            f'There\'s no service principal named "{name}". Add it under {SETTINGS_PATH}, or pick another.')
    if not sp.client_id_key or not sp.client_secret_key:
        raise ServicePrincipalError(
            f'Service principal "{name}" needs both a client ID key and a client secret key ({SETTINGS_PATH}).')
    scope = _scope()
    if not scope:
        raise ServicePrincipalError(
            "No secret scope is set. Set Secret scope under Admin → Settings → Target Workspaces; "
            "named service principals read their keys from it.")
    return _read_secret(scope, sp.client_id_key), _read_secret(scope, sp.client_secret_key)


def home_host() -> str:
    host = (getattr(settings, "DATABRICKS_HOST", "") or getattr(settings, "DATABRICKS_WORKSPACE_URL", "") or "").strip()
    if host:
        return host if "://" in host else f"https://{host}"
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient().config.host or ""


def workspace_client(name: str, host: Optional[str] = None) -> Any:
    """A ``WorkspaceClient`` authenticated as the named SP (OAuth M2M), on the home workspace by default."""
    from databricks.sdk import WorkspaceClient

    client_id, client_secret = credentials(name)
    return WorkspaceClient(
        host=host or home_host(), client_id=client_id, client_secret=client_secret, auth_type="oauth-m2m",
    )


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str
    application_id: str = ""
    display_name: str = ""


def check(name: str) -> CheckResult:
    """Sign in as the named SP on the home workspace and say who it is."""
    try:
        me = workspace_client(name).current_user.me()
    except ServicePrincipalError as exc:
        return CheckResult(name=name, ok=False, message=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.info("Service principal check failed for %s: %s", name, exc)
        return CheckResult(name=name, ok=False, message=(
            f"The secrets were read, but signing in as this service principal failed. Check the client ID and "
            f"secret belong together and that the SP is added to this workspace. ({str(exc)[:300]})"))
    app_id = str(getattr(me, "application_id", "") or getattr(me, "user_name", "") or "")
    display = str(getattr(me, "display_name", "") or "")
    label = f"{display} ({app_id})" if display and app_id else (display or app_id or "the service principal")
    return CheckResult(name=name, ok=True, message=f"Signed in as {label}.", application_id=app_id, display_name=display)


# --- Who uses which identity ----------------------------------------------------

def _target_label(ws: Dict[str, Any]) -> Tuple[str, str]:
    """``(identity, kind)`` for a target-workspace row, naming the SP list entry when the keys match one."""
    cid, csec = (ws.get("client_id_key") or "").strip(), (ws.get("client_secret_key") or "").strip()
    if not cid or not csec:
        return APP_SP_LABEL, "app"
    match = next((sp for sp in configured() if sp.client_id_key == cid and sp.client_secret_key == csec), None)
    if match:
        return f"{match.name} (from the list)", "named"
    return f"Its own keys: {cid} / {csec}", "target"


def usage() -> List[Dict[str, Any]]:
    """Which identity each part of the app runs as. Read from settings only; no secrets are read."""
    from app.core.workspaces import _configured_target_workspaces

    rows: List[Dict[str, Any]] = []

    export_sp = (getattr(settings, "RBAC_EXPORT_SERVICE_PRINCIPAL", "") or "").strip()
    if export_sp:
        defined = get(export_sp) is not None
        rows.append({
            "feature": "Access Rules exports",
            "detail": "Writes the exported CSVs to each export folder, and runs Test on the Exports tab.",
            "identity": export_sp, "kind": "named", "ok": defined,
            "problem": "" if defined else f'"{export_sp}" isn\'t in the list above, so exports will fail.',
            "configured_in": "Admin → Settings → Access Rules → Export service principal",
        })
    else:
        rows.append({
            "feature": "Access Rules exports",
            "detail": "Writes the exported CSVs to each export folder, and runs Test on the Exports tab.",
            "identity": APP_SP_LABEL, "kind": "app", "ok": True, "problem": "",
            "configured_in": "Admin → Settings → Access Rules → Export service principal",
        })

    targets = _configured_target_workspaces()
    by_name = {str(ws.get("name") or ""): ws for ws in targets}
    for ws in targets:
        identity, kind = _target_label(ws)
        rows.append({
            "feature": f"Target workspace {ws.get('name') or '(unnamed)'}",
            "detail": "OmniGuard scans, preview features, workspace checks and dashboard names in that workspace.",
            "identity": identity, "kind": kind, "ok": True, "problem": "",
            "configured_in": "Admin → Settings → Target Workspaces",
        })

    cert = (getattr(settings, "SENTINEL_DATA_CERT_WORKSPACE", "") or "").strip()
    gov_identity, gov_kind, gov_ok, gov_problem = APP_SP_LABEL, "app", True, ""
    if cert:
        ws = by_name.get(cert)
        if ws is None:
            gov_ok, gov_problem = False, f'"{cert}" isn\'t a target workspace, so the app\'s service principal is used.'
        else:
            gov_identity, gov_kind = _target_label(ws)
            gov_identity = f"{gov_identity}, via target workspace {cert}"
    rows.append({
        "feature": "Governance",
        "detail": "Data certification, Metadata Manager tag and comment changes, and the data catalog and lineage sync.",
        "identity": gov_identity, "kind": gov_kind, "ok": gov_ok, "problem": gov_problem,
        "configured_in": "Admin → Settings → Data Certification → Data certification workspace",
    })

    rows.append({
        "feature": "What people look up",
        "detail": "Unity Catalog searches and access checks in chat, data previews, skills and Genie.",
        "identity": "The signed-in person", "kind": "user", "ok": True, "problem": "",
        "configured_in": "Not configurable: answers must reflect each person's own access.",
    })
    rows.append({
        "feature": "Everything else",
        "detail": ("The app's database, the model and embeddings, Context Catalog and training files, reading "
                   "secrets, and the provisioning connection."),
        "identity": APP_SP_LABEL, "kind": "app", "ok": True, "problem": "",
        "configured_in": "Set when the app is deployed.",
    })
    return rows
