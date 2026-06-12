"""
Tool to list catalogs.
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError
import fnmatch

class GetCatalogListInput(BaseModel):
    target_host: str = Field(..., description="The host URL of the target Databricks workspace.")
    name_pattern: Optional[str] = Field(None, description="Optional. Exact name or glob pattern (e.g. '*dev*') to filter catalogs.")

@tool(
    name="get_catalog_list",
    description="Lists all available catalogs in the Databricks Unity Catalog for a specific workspace. You can optionally filter by a specific name or pattern to check if a catalog exists. NEXT STEP: Use 'get_schema_list' to explore a specific catalog.",
    args_schema=GetCatalogListInput
)
def get_catalog_list(target_host: str, name_pattern: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch the list of catalogs along with their descriptions.
    """
    try:
        from app.core.workspaces import get_workspace_config
        ws_config = get_workspace_config(target_host)
        if not ws_config:
            raise ValueError(f"Target host {target_host} not found in configuration.")
            
        provider = DatabricksProvider(
            host=ws_config.host,
            token=ws_config.token,
            client_id=ws_config.client_id,
            client_secret=ws_config.client_secret,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )
        
        catalogs = provider.client.catalogs.list()
        
        catalog_list = []
        for catalog in catalogs:
            if name_pattern and not fnmatch.fnmatch(catalog.name.lower(), name_pattern.lower()):
                continue
                
            catalog_list.append({
                "name": catalog.name,
                "comment": catalog.comment or "No description provided",
                "catalog_type": catalog.catalog_type.value if hasattr(catalog.catalog_type, 'value') else str(catalog.catalog_type),
                "owner": catalog.owner,
                "properties": catalog.properties or {}
            })
        
        return {
            "count": len(catalog_list),
            "catalogs": catalog_list
        }
        
    except Exception as e:
        raise RetryableError(f"Failed to fetch catalog list: {str(e)}")
