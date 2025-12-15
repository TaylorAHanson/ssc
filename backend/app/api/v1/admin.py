"""
Admin API endpoints for form management.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from app.agents.forms_registry import (
    get_form_schema,
    save_form_schema,
    list_forms,
    list_form_versions,
    get_form_version
)
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


class FormInfo(BaseModel):
    """Form information model."""
    path: str
    title: str
    filename: str


class FormVersionInfo(BaseModel):
    """Form version information model."""
    filename: str
    date: str
    is_active: bool


class FormSchemaResponse(BaseModel):
    """Form schema response model."""
    path: str
    schema: Dict[str, Any]


class SaveFormRequest(BaseModel):
    """Request model for saving a form."""
    schema: Dict[str, Any]
    create_version: bool = True


@router.get("/forms", response_model=List[FormInfo])
async def list_all_forms():
    """
    List all available forms.
    """
    try:
        forms = list_forms()
        return forms
    except Exception as e:
        logger.error(f"Error listing forms: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list forms: {str(e)}")


@router.get("/forms/{form_path:path}/versions", response_model=List[FormVersionInfo])
async def get_form_versions(form_path: str):
    """
    Get all versions of a form.
    
    Args:
        form_path: Route path (e.g., '/paas/workspace-access')
    """
    try:
        # Ensure path starts with /
        if not form_path.startswith("/"):
            form_path = f"/{form_path}"
        
        versions = list_form_versions(form_path)
        return versions
    except Exception as e:
        logger.error(f"Error getting form versions for {form_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get form versions: {str(e)}")


@router.get("/forms/{form_path:path}", response_model=FormSchemaResponse)
async def get_form(form_path: str, version: Optional[str] = None):
    """
    Get a specific form schema.
    
    Args:
        form_path: Route path (e.g., '/paas/workspace-access')
        version: Optional version filename to get a specific version
    """
    try:
        # Ensure path starts with /
        if not form_path.startswith("/"):
            form_path = f"/{form_path}"
        
        if version:
            schema = get_form_version(form_path, version)
        else:
            schema = get_form_schema(form_path)
        
        if not schema:
            raise HTTPException(status_code=404, detail=f"Form not found: {form_path}")
        
        return FormSchemaResponse(path=form_path, schema=schema)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting form {form_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get form: {str(e)}")


@router.put("/forms/{form_path:path}", response_model=FormSchemaResponse)
async def save_form(form_path: str, request: SaveFormRequest):
    """
    Save or update a form schema.
    
    Args:
        form_path: Route path (e.g., '/paas/workspace-access')
        request: Form schema and options
    """
    try:
        # Ensure path starts with /
        if not form_path.startswith("/"):
            form_path = f"/{form_path}"
        
        # Validate schema has required structure
        if not isinstance(request.schema, dict):
            raise HTTPException(status_code=400, detail="Schema must be a JSON object")
        
        # Save the form
        success = save_form_schema(
            form_path,
            request.schema,
            create_version=request.create_version
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save form")
        
        # Return the saved form
        return FormSchemaResponse(path=form_path, schema=request.schema)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving form {form_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save form: {str(e)}")

