from fastapi import APIRouter
from app.core.config import settings

router = APIRouter()

@router.get("/")
async def get_branding():
    """Get brand-specific settings for the frontend."""
    return {
        "brand_name": settings.BRAND_NAME,
        "brand_logo_url": settings.BRAND_LOGO_URL,
        "brand_color_primary": settings.BRAND_COLOR_PRIMARY,
        "brand_color_secondary": settings.BRAND_COLOR_SECONDARY,
        "brand_color_info": settings.BRAND_COLOR_INFO,
        "brand_color_alert": settings.BRAND_COLOR_ALERT,
        "brand_color_warning": settings.BRAND_COLOR_WARNING,
        "brand_color_success": settings.BRAND_COLOR_SUCCESS,
    }
