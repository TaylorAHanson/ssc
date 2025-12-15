"""
Form schemas registry for all SurveyJS forms.
This provides the actual form structure to the LLM so it can generate accurate prefill data.

Forms are stored as JSON files in the app/forms/ directory.
"""
import json
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

# Path to forms directory
FORMS_DIR = Path(__file__).parent.parent / "forms"


def _route_to_filename(form_path: str) -> str:
    """Convert route path to filename (e.g., '/paas/workspace-access' -> 'paas-workspace-access.json')."""
    # Remove leading slash and replace remaining slashes with hyphens
    filename = form_path.lstrip("/").replace("/", "-")
    return f"{filename}.json"


def _filename_to_route(filename: str) -> str:
    """Convert filename to route path (e.g., 'paas-workspace-access.json' -> '/paas/workspace-access')."""
    # Remove .json extension and replace hyphens with slashes
    route = filename.replace(".json", "").replace("-", "/")
    return f"/{route}"


def _get_version_filename(base_filename: str, date_str: str) -> str:
    """Get version filename with date appended (e.g., 'form.json' -> 'form-2024-01-15.json')."""
    name, ext = os.path.splitext(base_filename)
    return f"{name}-{date_str}{ext}"


def _is_version_file(filename: str) -> bool:
    """Check if a filename is a version file (e.g., 'form-2025-12-15-160130.json')."""
    # Version files have pattern: {base}-YYYY-MM-DD-HHMMSS.json
    # Match pattern: ends with -YYYY-MM-DD-HHMMSS.json where HHMMSS is 6 digits
    version_pattern = r'-\d{4}-\d{2}-\d{2}-\d{6}\.json$'
    return bool(re.search(version_pattern, filename))


def get_form_schema(form_path: str) -> Dict[str, Any]:
    """Get the SurveyJS form schema for a given form path."""
    filename = _route_to_filename(form_path)
    filepath = FORMS_DIR / filename
    
    if not filepath.exists():
        return {}
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        # Log error but return empty dict
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error loading form schema from {filepath}: {e}")
        return {}


def save_form_schema(form_path: str, schema: Dict[str, Any], create_version: bool = True) -> bool:
    """
    Save a form schema to file.
    
    Args:
        form_path: Route path (e.g., '/paas/workspace-access')
        schema: Form schema dictionary
        create_version: If True, create a backup version of the existing file before saving
    
    Returns:
        True if successful, False otherwise
    """
    filename = _route_to_filename(form_path)
    filepath = FORMS_DIR / filename
    
    # Create backup version if requested and file exists
    if create_version and filepath.exists():
        try:
            date_str = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            version_filename = _get_version_filename(filename, date_str)
            version_filepath = FORMS_DIR / version_filename
            
            # Copy existing file to version file
            import shutil
            shutil.copy2(filepath, version_filepath)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to create version backup: {e}")
    
    # Save new schema
    try:
        # Ensure directory exists
        FORMS_DIR.mkdir(parents=True, exist_ok=True)
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(schema, f, indent=2, ensure_ascii=False)
        return True
    except (IOError, OSError) as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error saving form schema to {filepath}: {e}")
        return False


def list_forms() -> List[Dict[str, Any]]:
    """
    List all available forms (excluding version files).
    
    Returns:
        List of form info dictionaries with 'path' and 'title' keys
    """
    forms = []
    
    if not FORMS_DIR.exists():
        return forms
    
    for filename in sorted(FORMS_DIR.glob("*.json")):
        # Skip version files (those with date patterns like -2025-12-15-160130.json)
        if _is_version_file(filename.name):
            continue
        
        route = _filename_to_route(filename.name)
        schema = get_form_schema(route)
        
        # Extract title from form (use first page title or route-based title)
        title = route.replace("/", " ").replace("-", " ").title()
        if schema.get("pages") and len(schema["pages"]) > 0:
            first_page = schema["pages"][0]
            if first_page.get("title"):
                title = first_page["title"]
        
        forms.append({
            "path": route,
            "title": title,
            "filename": filename.name
        })
    
    return forms


def list_form_versions(form_path: str) -> List[Dict[str, Any]]:
    """
    List all versions of a form (including the active version).
    
    Args:
        form_path: Route path (e.g., '/paas/workspace-access')
    
    Returns:
        List of version info dictionaries with 'filename', 'date', 'is_active' keys
    """
    base_filename = _route_to_filename(form_path)
    base_name = os.path.splitext(base_filename)[0]
    
    versions = []
    
    if not FORMS_DIR.exists():
        return versions
    
    # Add active version
    active_filepath = FORMS_DIR / base_filename
    if active_filepath.exists():
        stat = active_filepath.stat()
        versions.append({
            "filename": base_filename,
            "date": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "is_active": True
        })
    
    # Find version files (base_name-YYYY-MM-DD-HHMMSS.json)
    # Use glob to find potential version files, then filter with _is_version_file
    pattern = f"{base_name}-*.json"
    for version_file in FORMS_DIR.glob(pattern):
        if version_file.name == base_filename:
            continue  # Skip active version
        
        # Only include files that match the version file pattern
        if _is_version_file(version_file.name):
            try:
                stat = version_file.stat()
                versions.append({
                    "filename": version_file.name,
                    "date": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "is_active": False
                })
            except Exception:
                pass
    
    # Sort by date (newest first)
    versions.sort(key=lambda x: x["date"], reverse=True)
    
    return versions


def get_form_version(form_path: str, version_filename: Optional[str] = None) -> Dict[str, Any]:
    """
    Get a specific version of a form.
    
    Args:
        form_path: Route path (e.g., '/paas/workspace-access')
        version_filename: Optional specific version filename. If None, returns active version.
    
    Returns:
        Form schema dictionary
    """
    if version_filename:
        filepath = FORMS_DIR / version_filename
    else:
        filename = _route_to_filename(form_path)
        filepath = FORMS_DIR / filename
    
    if not filepath.exists():
        return {}
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error loading form version from {filepath}: {e}")
        return {}


def format_form_schema_for_prompt(form_schema: Dict[str, Any]) -> str:
    """Format form schema for inclusion in LLM prompt."""
    return json.dumps(form_schema, indent=2)

