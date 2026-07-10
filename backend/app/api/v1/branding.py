import re
from typing import Any, Dict, List

from fastapi import APIRouter
from app.core.config import settings, _yaml_config

router = APIRouter()

# Sidebar group an embedded app lands in when its entry omits `group`.
_DEFAULT_EMBEDDED_APP_GROUP = "Build & Customize"
_DEFAULT_EMBEDDED_APP_ICON = "LayoutDashboard"
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slug(value: str) -> str:
    """Identifier-safe slug for an embedded-app id (lowercase, [a-z0-9_-])."""
    return _SLUG_RE.sub("-", (value or "").strip().lower()).strip("-_")


def _normalize_embedded_app(raw: Dict[str, Any]) -> Dict[str, Any] | None:
    """Coerce one raw `embedded_apps` entry into the frontend contract.

    Returns ``None`` for entries missing the two things that make an embed
    usable: a stable ``id`` and a ``url`` to frame. Everything else has a
    sane default so a minimal ``{id, title, url}`` entry just works.
    """
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()
    app_id = _slug(str(raw.get("id") or raw.get("title") or ""))
    if not url or not app_id:
        return None
    personas = raw.get("allowed_personas")
    if isinstance(personas, str):
        personas = [personas]
    if not isinstance(personas, list):
        personas = None
    return {
        "id": app_id,
        "title": str(raw.get("title") or app_id).strip(),
        "url": url,
        "icon": str(raw.get("icon") or _DEFAULT_EMBEDDED_APP_ICON).strip(),
        "group": str(raw.get("group") or _DEFAULT_EMBEDDED_APP_GROUP).strip()
        or _DEFAULT_EMBEDDED_APP_GROUP,
        "description": str(raw.get("description") or "").strip(),
        # Omit the key entirely (rather than null) when ungated so the
        # frontend's "no personas = everyone" default applies cleanly.
        **({"allowed_personas": [str(p) for p in personas]} if personas else {}),
    }


def _embedded_apps() -> List[Dict[str, Any]]:
    """Resolve the configured embedded apps for the frontend.

    Reads the generic ``embedded_apps:`` list, preserving its order (apps
    render in the order listed, within their sidebar group) and dropping
    malformed/duplicate entries.
    """
    raw_list = _yaml_config.get("embedded_apps") or []
    apps: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    if isinstance(raw_list, list):
        for raw in raw_list:
            app = _normalize_embedded_app(raw)
            if app and app["id"] not in seen_ids:
                apps.append(app)
                seen_ids.add(app["id"])
    return apps


def _normalize_workspace_url(raw: str) -> str:
    """Return a clean ``https://<host>`` workspace URL with no trailing slash.

    Accepts inputs like ``adb-123.cloud.databricks.com``, ``https://adb-123…/``,
    or empty strings (returns empty). Used by the frontend to deep-link into
    Databricks (Catalog Explorer, Dashboards, Jobs, Apps, Genie).
    """
    if not raw:
        return ""
    url = raw.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _genie_poll_timeout_seconds(default: int = 300) -> int:
    """Resolve the Genie poll window (seconds) from configuration.

    Reads ``tools.ask_your_data.poll_timeout_seconds`` and coerces it to a
    sane positive int, falling back to ``default`` on a missing/garbage value.
    """
    tools_cfg = _yaml_config.get("tools") or {}
    entry = tools_cfg.get("ask_your_data")
    if isinstance(entry, dict):
        try:
            val = int(entry.get("poll_timeout_seconds", default))
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    return default


@router.get("")
@router.get("/")
async def get_branding():
    """Get brand-specific settings and feature flags for the frontend."""
    workspace_url = _normalize_workspace_url(
        settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL
    )
    return {
        "brand_name": settings.BRAND_NAME,
        "brand_short_name": settings.BRAND_SHORT_NAME or settings.BRAND_NAME,
        "brand_logo_url": settings.BRAND_LOGO_URL,
        "brand_color_primary": settings.BRAND_COLOR_PRIMARY,
        "brand_color_secondary": settings.BRAND_COLOR_SECONDARY,
        "brand_color_info": settings.BRAND_COLOR_INFO,
        "brand_color_alert": settings.BRAND_COLOR_ALERT,
        "brand_color_warning": settings.BRAND_COLOR_WARNING,
        "brand_color_success": settings.BRAND_COLOR_SUCCESS,
        "databricks_workspace_url": workspace_url,
        # Config-driven list of iframe-embedded apps. Each renders a sidebar
        # link that opens the app in-page at /embedded/<id>, in list order.
        "embedded_apps": _embedded_apps(),
        # External "open in Databricks" link surfaced inside the
        # Ask Your Data page. Falls back to /one (Genie home) when not
        # explicitly configured.
        "genie_full_experience_url": _yaml_config.get("links", {}).get(
            "genie_full_experience_url", ""
        ),
        # Client-side Genie poll window (seconds). Sourced from
        # tools.ask_your_data.poll_timeout_seconds; the chat keeps polling for
        # an answer up to this long before surfacing a timeout. Not a Databricks
        # limit — each poll is a short request.
        "genie_poll_timeout_seconds": _genie_poll_timeout_seconds(),
        "features": _yaml_config.get("features", {}),
        "tools": _yaml_config.get("tools", {}),
        "ui": _yaml_config.get("ui", {}),
        # Config-driven Self-Service Center catalog (categories + quick-action
        # cards) shown as an alternate landing view to the Assistant chat.
        "self_service_center": _yaml_config.get("self_service_center", {}),
        # Config-driven Community Links page (categories of external resources).
        "community_links": _yaml_config.get("community_links", {}),
        # When true, this environment locks in-place workflow (Workflow) authoring;
        # the frontend hides edit/publish/delete and steers admins to bundle import.
        "workflow_authoring_locked": settings.WORKFLOW_AUTHORING_LOCKED,
        # Global site-wide banner ({active, type, message}). Edited live under
        # Admin -> Settings -> System Banner; the frontend shows it when active.
        "system_banner": _yaml_config.get("banner") or {},
    }
