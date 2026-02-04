"""
Content registry for community content (links, events, assets, training).
This provides the content to the LLM and the Admin UI.

Content is stored as JSON files in the app/content/ directory.
"""
import json
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

# Path to content directory
CONTENT_DIR = Path(__file__).parent.parent / "content"


def _get_version_filename(base_filename: str, date_str: str) -> str:
    """Get version filename with date appended (e.g., 'links.json' -> 'links-2024-01-15.json')."""
    name, ext = os.path.splitext(base_filename)
    return f"{name}-{date_str}{ext}"


def _is_version_file(filename: str) -> bool:
    """Check if a filename is a version file (e.g., 'links-2025-12-15-160130.json')."""
    # Version files have pattern: {base}-YYYY-MM-DD-HHMMSS.json
    version_pattern = r'-\d{4}-\d{2}-\d{2}-\d{6}\.json$'
    return bool(re.search(version_pattern, filename))


def get_content(filename: str) -> Dict[str, Any]:
    """Get the content for a given filename."""
    # Ensure filename ends with .json
    if not filename.endswith(".json"):
        filename = f"{filename}.json"
        
    filepath = CONTENT_DIR / filename
    
    if not filepath.exists():
        return {}
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error loading content from {filepath}: {e}")
        return {}


def save_content(filename: str, content: Dict[str, Any], create_version: bool = True) -> bool:
    """
    Save content to file.
    
    Args:
        filename: Filename (e.g., 'community-links.json')
        content: Content dictionary
        create_version: If True, create a backup version of the existing file before saving
    
    Returns:
        True if successful, False otherwise
    """
    # Ensure filename ends with .json
    if not filename.endswith(".json"):
        filename = f"{filename}.json"
        
    filepath = CONTENT_DIR / filename
    
    # Create backup version if requested and file exists
    if create_version and filepath.exists():
        try:
            date_str = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            version_filename = _get_version_filename(filename, date_str)
            version_filepath = CONTENT_DIR / version_filename
            
            # Copy existing file to version file
            import shutil
            shutil.copy2(filepath, version_filepath)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to create version backup: {e}")
    
    # Save new content
    try:
        # Ensure directory exists
        CONTENT_DIR.mkdir(parents=True, exist_ok=True)
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2, ensure_ascii=False)
        return True
    except (IOError, OSError) as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error saving content to {filepath}: {e}")
        return False


def list_content() -> List[Dict[str, Any]]:
    """
    List all available content files (excluding version files).
    
    Returns:
        List of content info dictionaries with 'filename' and 'title' keys
    """
    items = []
    
    if not CONTENT_DIR.exists():
        return items
    
    for filepath in sorted(CONTENT_DIR.glob("*.json")):
        # Skip version files
        if _is_version_file(filepath.name):
            continue
            
        # Skip events.json as it is managed by live sync
        if filepath.name == "events.json":
            continue
        
        # Generate a title from filename
        title = filepath.stem.replace("-", " ").title()
        
        items.append({
            "filename": filepath.name,
            "title": title
        })
    
    return items


def list_content_versions(filename: str) -> List[Dict[str, Any]]:
    """
    List all versions of a content file.
    """
    # Ensure filename ends with .json
    if not filename.endswith(".json"):
        filename = f"{filename}.json"
        
    base_name = os.path.splitext(filename)[0]
    versions = []
    
    if not CONTENT_DIR.exists():
        return versions
    
    # Add active version
    active_filepath = CONTENT_DIR / filename
    if active_filepath.exists():
        stat = active_filepath.stat()
        versions.append({
            "filename": filename,
            "date": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "is_active": True
        })
    
    # Find version files
    pattern = f"{base_name}-*.json"
    for version_file in CONTENT_DIR.glob(pattern):
        if version_file.name == filename:
            continue
            
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
                
    versions.sort(key=lambda x: x["date"], reverse=True)
    return versions

def get_content_version(filename: str, version_filename: Optional[str] = None) -> Dict[str, Any]:
    """Get a specific version of content."""
    target_file = version_filename if version_filename else filename
    # Ensure filename ends with .json
    if not target_file.endswith(".json"):
        target_file = f"{target_file}.json"

    filepath = CONTENT_DIR / target_file
    
    if not filepath.exists():
        return {}
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error loading content version {target_file}: {e}")
        return {}

