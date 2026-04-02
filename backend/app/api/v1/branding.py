from fastapi import APIRouter
from app.core.config import settings, _yaml_config

router = APIRouter()

@router.get("")
@router.get("/")
async def get_branding():
    """Get brand-specific settings and feature flags for the frontend."""
    return {
        "brand_name": settings.BRAND_NAME,
        "brand_logo_url": settings.BRAND_LOGO_URL,
        "brand_color_primary": settings.BRAND_COLOR_PRIMARY,
        "brand_color_secondary": settings.BRAND_COLOR_SECONDARY,
        "brand_color_info": settings.BRAND_COLOR_INFO,
        "brand_color_alert": settings.BRAND_COLOR_ALERT,
        "brand_color_warning": settings.BRAND_COLOR_WARNING,
        "brand_color_success": settings.BRAND_COLOR_SUCCESS,
        "features": _yaml_config.get("features", {}),
        "tools": _yaml_config.get("tools", {}),
        "workflows": _yaml_config.get("workflows", {}),
        "ui": _yaml_config.get("ui", {}),
    }
