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
    catalog_name: str = Field(..., description="Name of the catalog to list schemas for")
    name_pattern: Optional[str] = Field(None, description="Optional. Exact name or glob pattern (e.g. '*dev*') to filter for a specific schema.")

@tool(
    name="get_schema_list",
    description="Lists all schemas within a specified catalog in Unity Catalog, including their descriptions/comments. You can optionally filter by a specific name or pattern to check if a schema exists. NEXT STEP: Use 'get_table_list' to find datasets within a schema.",
    args_schema=GetSchemaListInput
)
async def get_schema_list(catalog_name: str, name_pattern: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch the list of schemas for a catalog along with their descriptions.
    """
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
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
