"""
Tool to list schemas.
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError
import fnmatch

class GetSchemaListInput(BaseModel):
    target_host: str = Field(..., description="The host URL of the target Databricks workspace.")
    catalog_name: str = Field(..., description="Name of the catalog to list schemas for")
    name_pattern: Optional[str] = Field(None, description="Optional. Exact name or glob pattern (e.g. '*dev*') to filter for a specific schema.")

@tool(
    name="get_schema_list",
    description="Lists all schemas within a specified catalog in Unity Catalog for a specific workspace. You can optionally filter by a specific name or pattern to check if a schema exists. NEXT STEP: Use 'get_table_list' to find datasets within a schema.",
    args_schema=GetSchemaListInput
)
def get_schema_list(target_host: str, catalog_name: str, name_pattern: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch the list of schemas for a catalog along with their descriptions.
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
        
        schemas = provider.client.schemas.list(catalog_name=catalog_name)
        
        schema_list = []
        for schema in schemas:
            if name_pattern and not fnmatch.fnmatch(schema.name.lower(), name_pattern.lower()):
                continue
                
            schema_list.append({
                "name": schema.name,
                "catalog_name": schema.catalog_name,
                "comment": schema.comment or "No description provided",
                "owner": schema.owner,
                "properties": schema.properties or {}
            })
        
        return {
            "catalog_name": catalog_name,
            "count": len(schema_list),
            "schemas": schema_list
        }
        
    except Exception as e:
        raise RetryableError(f"Failed to fetch schema list for catalog '{catalog_name}': {str(e)}")
