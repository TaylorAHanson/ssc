"""
Tool to list catalogs.
"""
from typing import Dict, Any
from pydantic import BaseModel
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class GetCatalogListInput(BaseModel):
    pass

@tool(
    name="get_catalog_list",
    description="Lists all available catalogs in the Databricks Unity Catalog, including their descriptions and comments. Use this to discover available catalogs or to find similar catalog names if a specific one is not found. NEXT STEP: Use 'get_schema_list' to explore a specific catalog.",
    args_schema=GetCatalogListInput
)
async def get_catalog_list() -> Dict[str, Any]:
    """
    Fetch the list of catalogs along with their descriptions.
    """
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )
        
        catalogs = provider.client.catalogs.list()
        
        catalog_list = []
        for catalog in catalogs:
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
