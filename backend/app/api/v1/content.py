"""
API endpoints for managing dynamic content (community links, events, etc.).
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Union
from app.agents.content_registry import (
    get_content,
    save_content,
    list_content,
    list_content_versions,
    get_content_version
)
from app.workers.tasks.sync_calendar import sync_calendar_task
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


class ContentInfo(BaseModel):
    """Content file information."""
    filename: str
    title: str


class ContentVersionInfo(BaseModel):
    """Content version information."""
    filename: str
    date: str
    is_active: bool


class SaveContentRequest(BaseModel):
    """Request model for saving content."""
    content: Union[Dict[str, Any], List[Any]]
    create_version: bool = True


@router.get("/content", response_model=List[ContentInfo])
async def list_all_content():
    """List all available content files."""
    try:
        return list_content()
    except Exception as e:
        logger.error(f"Error listing content: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/content/{filename}", response_model=Union[Dict[str, Any], List[Any]])
async def get_content_file(filename: str, version: Optional[str] = None):
    """Get specific content file."""
    try:
        if version:
            content = get_content_version(filename, version)
        else:
            content = get_content(filename)
            
        if not content and not version:
            # If requesting active version and empty, might not exist
            # But get_content returns {} if not found or empty. 
            # We should check if file exists implicitly by the registry result
            # Ideally registry raises or returns None for not found.
            # Current implementation returns {}. We'll assume empty dict is valid content or empty file.
            pass
            
        return content
    except Exception as e:
        logger.error(f"Error getting content {filename}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/content/{filename}/versions", response_model=List[ContentVersionInfo])
async def get_versions(filename: str):
    """Get versions of a content file."""
    try:
        return list_content_versions(filename)
    except Exception as e:
        logger.error(f"Error getting versions for {filename}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/content/{filename}")
async def update_content(filename: str, request: SaveContentRequest):
    """Update a content file."""
    try:
        success = save_content(
            filename,
            request.content,
            create_version=request.create_version
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save content")
            
        return {"status": "success", "filename": filename}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving content {filename}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/calendar/sync")
async def trigger_calendar_sync():
    """Manually trigger a calendar sync."""
    try:
        # Note: sync_calendar_task has its own interval check, but 
        # for manual triggering we explicitly bypass it.
        await sync_calendar_task(force=True)
        return {"status": "success", "message": "Calendar sync triggered"}
    except Exception as e:
        logger.error(f"Error triggering calendar sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))

