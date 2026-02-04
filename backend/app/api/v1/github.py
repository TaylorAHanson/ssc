"""
API endpoints for GitHub operations.
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Dict, Any
from app.api.deps import get_github_provider
from app.providers.github.client import GitHubProvider
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/templates", response_model=List[Dict[str, Any]])
async def list_github_templates(
    github: GitHubProvider = Depends(get_github_provider)
):
    """
    List all repositories marked as templates in the organization.
    This provides dynamic template discovery for the Reusable Assets UI.
    """
    try:
        templates = await github.list_templates()
        return templates
    except Exception as e:
        logger.error(f"Failed to list GitHub templates: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
