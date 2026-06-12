from fastapi import APIRouter
from app.core.config import settings, _yaml_config

router = APIRouter()


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


@router.get("")
@router.get("/")
async def get_branding():
    """Get brand-specific settings and feature flags for the frontend."""
    workspace_url = _normalize_workspace_url(
        settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL
    )
    return {
        "brand_name": settings.BRAND_NAME,
        "brand_logo_url": settings.BRAND_LOGO_URL,
        "brand_color_primary": settings.BRAND_COLOR_PRIMARY,
        "brand_color_secondary": settings.BRAND_COLOR_SECONDARY,
        "brand_color_info": settings.BRAND_COLOR_INFO,
        "brand_color_alert": settings.BRAND_COLOR_ALERT,
        "brand_color_warning": settings.BRAND_COLOR_WARNING,
        "brand_color_success": settings.BRAND_COLOR_SUCCESS,
        "databricks_workspace_url": workspace_url,
        "command_center_url": _yaml_config.get("branding", {}).get("command_center_url", ""),
        # External "open in Databricks" link surfaced inside the
        # Ask Your Data page. Falls back to /one (Genie home) when not
        # explicitly configured.
        "genie_full_experience_url": _yaml_config.get("links", {}).get(
            "genie_full_experience_url", ""
        ),
        "features": _yaml_config.get("features", {}),
        "tools": _yaml_config.get("tools", {}),
        "ui": _yaml_config.get("ui", {}),
        # When true, this environment locks in-place workflow (Workflow) authoring;
        # the frontend hides edit/publish/delete and steers admins to bundle import.
        "workflow_authoring_locked": settings.WORKFLOW_AUTHORING_LOCKED,
    }
