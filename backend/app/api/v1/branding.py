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
    }
