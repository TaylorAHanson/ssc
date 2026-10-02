"""Thin client for the workspace Settings v2 API (preview discovery and switching).

Calls go through ``api_client.do`` rather than the typed SDK methods: the SDK's
``SettingsMetadata`` class has no ``preview_phase`` field and silently drops it,
and ``preview_phase`` is the whole point of discovery.

* ``GET  /api/2.1/settings-metadata``  any workspace user
* ``GET  /api/2.1/settings/{name}``    any workspace user; ``effective_*`` is the
  value after defaults and higher-scope (account) overrides; the stored value
  (e.g. ``boolean_val``) is present only when the setting was set on this
  workspace itself.
* ``PATCH /api/2.1/settings/{name}``   workspace admin. There is no delete, so a
  setting can't be reset to "inherited" once it's been set here.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.workspaces import WorkspaceConfig

logger = logging.getLogger(__name__)

PREVIEW_PHASES = ("BETA", "PUBLIC_PREVIEW", "PRIVATE_PREVIEW")
_PAGE_SIZE = 1000
_MAX_PAGES = 20


class SettingsApiError(Exception):
    """A Settings v2 call failed (permission, unknown setting, network)."""


def build_client(ws: WorkspaceConfig):
    """A WorkspaceClient for ``ws`` with the app's bounded HTTP timeouts."""
    from app.providers.databricks.client import _build_workspace_client

    if ws.client_id and ws.client_secret:
        return _build_workspace_client(host=ws.host, client_id=ws.client_id, client_secret=ws.client_secret)
    if ws.token:
        return _build_workspace_client(host=ws.host, token=ws.token, auth_type="pat")
    # Local dev: fall through to the SDK's ambient auth chain (CLI profile).
    return _build_workspace_client(host=ws.host)


def list_metadata(client) -> List[Dict[str, Any]]:
    """Every setting the workspace lists, following ``next_page_token``."""
    items: List[Dict[str, Any]] = []
    token: Optional[str] = None
    for _ in range(_MAX_PAGES):
        query: Dict[str, Any] = {"page_size": _PAGE_SIZE}
        if token:
            query["page_token"] = token
        try:
            res = client.api_client.do("GET", "/api/2.1/settings-metadata", query=query) or {}
        except Exception as e:  # noqa: BLE001 - surface one error type to callers
            raise SettingsApiError(str(e)) from e
        items.extend(res.get("settings_metadata") or [])
        token = res.get("next_page_token")
        if not token:
            break
    return items


def value_type_of(type_json: Any) -> str:
    """``boolean`` when the setting's value schema is ``{"boolean_val": ...}``."""
    schema = type_json
    if isinstance(type_json, str):
        try:
            schema = json.loads(type_json)
        except ValueError:
            return "other"
    if isinstance(schema, dict) and set(schema.keys()) == {"boolean_val"}:
        return "boolean"
    return "other"


@dataclass
class ObservedValue:
    effective: Optional[bool]
    # True when the stored value was set on this workspace (not inherited).
    set_here: bool
    raw: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"effective": self.effective, "set_here": self.set_here}


def parse_value(res: Dict[str, Any]) -> ObservedValue:
    eff = (res.get("effective_boolean_val") or {}).get("value") if isinstance(res, dict) else None
    stored = res.get("boolean_val") if isinstance(res, dict) else None
    return ObservedValue(
        effective=bool(eff) if eff is not None else (False if "effective_boolean_val" in (res or {}) else None),
        set_here=stored is not None,
        raw=res or {},
    )


def get_value(client, name: str) -> ObservedValue:
    try:
        res = client.api_client.do("GET", f"/api/2.1/settings/{name}") or {}
    except Exception as e:  # noqa: BLE001
        raise SettingsApiError(str(e)) from e
    return parse_value(res)


def set_boolean(client, name: str, value: bool) -> ObservedValue:
    """PATCH a boolean setting on the workspace; returns the new value."""
    body = {"name": name, "boolean_val": {"value": bool(value)}}
    try:
        res = client.api_client.do("PATCH", f"/api/2.1/settings/{name}", body=body) or {}
    except Exception as e:  # noqa: BLE001
        raise SettingsApiError(str(e)) from e
    return parse_value(res)
