import asyncio
from typing import Dict, Any, Literal
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError
import logging

logger = logging.getLogger(__name__)

class FindOwnerInput(BaseModel):
    object_type: str = Field(..., description="Type of object to check. Supported: 'catalog', 'schema', 'table', 'job', 'dashboard', 'notebook', 'genie_space'.")
    object_name: str = Field(..., description="Full name (for catalog/schema/table/notebook) or ID (for job/dashboard/genie_space) of the object")

@tool(
    name="find_owner",
    description="Finds the owner of a specified Databricks object (catalog, schema, table, job, dashboard, notebook, genie_space).",
    args_schema=FindOwnerInput
)
async def find_owner(object_type: str, object_name: str) -> Dict[str, Any]:
    """
    Finds the owner of a Databricks object.
    
    Args:
        object_type: Type of object
        object_name: The full name or ID of the object
    """
    try:
        # Instantiate provider
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )
        
        # SDK-based objects (All types)
        if object_type == "catalog":
            return await _find_catalog_owner(provider, object_name)
            
        if object_type == "schema":
            return await _find_schema_owner(provider, object_name)
            
        if object_type == "table":
            return await _find_table_owner(provider, object_name)

        if object_type == "job":
            return await _find_job_owner(provider, object_name)
            
        if object_type == "dashboard":
            return await _find_dashboard_owner(provider, object_name)
            
        if object_type == "notebook":
            return await _find_notebook_owner(provider, object_name)
            
        if object_type == "genie_space":
            return await _find_genie_space_owner(provider, object_name)

        return {
            "found": False,
            "message": f"Finding owner for '{object_type}' is not yet implemented. Supported types: catalog, schema, table, job, dashboard, notebook, genie_space.",
            "object_type": object_type,
            "object_name": object_name
        }
        
    except RetryableError as e:
        raise
    except Exception as e:
        # Generic error handling handles SDK errors too
        return {
            "found": False,
            "message": f"Failed to find owner for {object_type} '{object_name}': {str(e)}",
            "object_type": object_type,
            "object_name": object_name
        }

async def _find_catalog_owner(provider: DatabricksProvider, name: str) -> Dict[str, Any]:
    try:
        cat = await asyncio.to_thread(provider.client.catalogs.get, name)
        return {"found": True, "owner": cat.owner or "Unknown", "object_type": "catalog", "object_name": name}
    except Exception as e:
        # SDK throws error if not found
        return {"found": False, "message": f"Catalog not found: {str(e)}", "object_type": "catalog", "object_name": name}

async def _find_schema_owner(provider: DatabricksProvider, full_name: str) -> Dict[str, Any]:
    try:
        schema = await asyncio.to_thread(provider.client.schemas.get, full_name)
        return {"found": True, "owner": schema.owner or "Unknown", "object_type": "schema", "object_name": full_name}
    except Exception as e:
        return {"found": False, "message": f"Schema not found: {str(e)}", "object_type": "schema", "object_name": full_name}

async def _find_table_owner(provider: DatabricksProvider, full_name: str) -> Dict[str, Any]:
    try:
        table = await asyncio.to_thread(provider.client.tables.get, full_name)
        return {"found": True, "owner": table.owner or "Unknown", "object_type": "table", "object_name": full_name}
    except Exception as e:
        return {"found": False, "message": f"Table not found: {str(e)}", "object_type": "table", "object_name": full_name}

async def _find_job_owner(provider: DatabricksProvider, job_id: str) -> Dict[str, Any]:
    try:
        # job_id must be int
        job_id_int = int(job_id)
        job = await asyncio.to_thread(provider.client.jobs.get, job_id_int)
        
        owner = job.creator_user_name or "Unknown"
        return {
            "found": True, 
            "owner": owner,
            "object_type": "job",
            "object_name": job_id,
            "details": {"name": job.settings.name if job.settings else "Unknown"}
        }
    except Exception as e:
        return {"found": False, "message": f"Job not found or error: {str(e)}", "object_type": "job", "object_name": job_id}

async def _find_dashboard_owner(provider: DatabricksProvider, dashboard_id: str) -> Dict[str, Any]:
    try:
        # Try Lakeview (AI/BI Dashboards)
        dash = await asyncio.to_thread(provider.client.lakeview.get, dashboard_id)
        # Lakeview object usually has 'last_modified_by' or we might need permissions
        # Just return "Unknown" if not obvious, but let's check structure if we can
        # Assuming typical serialized object
        return {
            "found": True,
            "owner": "Unknown (Dashboard found)", # Lakeview API response structure needs verification for specific owner field
            "object_type": "dashboard", 
            "object_name": dashboard_id,
            "details": {"display_name": dash.display_name}
        }
    except Exception as e:
        # Fallback to Legacy Dashboards? (Not supported by public SDK usually)
        return {"found": False, "message": f"Dashboard not found: {str(e)}", "object_type": "dashboard", "object_name": dashboard_id}

async def _find_notebook_owner(provider: DatabricksProvider, path: str) -> Dict[str, Any]:
    try:
        info = await asyncio.to_thread(provider.client.workspace.get_status, path)
        
        # Heuristic: /Users/<email>/...
        if path.startswith("/Users/"):
            parts = path.split('/')
            if len(parts) > 2:
                return {
                    "found": True,
                    "owner": parts[2],
                    "object_type": "notebook",
                    "object_name": path,
                    "details": {"heuristic": "Path-based"}
                }
        
        # TODO: Check permissions API for object owner if not in /Users/
        return {
            "found": True,
            "owner": "Unknown (Shared Path)",
            "object_type": "notebook",
            "object_name": path,
            "message": "Located in shared path, owner cannot be determined by path alone."
        }
    except Exception as e:
        return {"found": False, "message": f"Notebook not found: {str(e)}", "object_type": "notebook", "object_name": path}

async def _find_genie_space_owner(provider: DatabricksProvider, space_id: str) -> Dict[str, Any]:
    try:
        space = await asyncio.to_thread(provider.client.genie.spaces.get, space_id)
        # Check space object for owner/creator
        # Note: SDK definitions vary, relying on generic availability
        return {
            "found": True,
            "owner": "Unknown (Genie Space found)", 
            "object_type": "genie_space",
            "object_name": space_id,
            "details": {"name": getattr(space, "name", "Unknown")}
        }
    except Exception as e:
        return {"found": False, "message": f"Genie Space not found: {str(e)}", "object_type": "genie_space", "object_name": space_id}
