"""
Tool to check if a catalog exists.
"""
from typing import Dict, Any
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class DoesCatalogExistInput(BaseModel):
    catalog_name: str = Field(..., description="Name of the catalog to check")

@tool(
    name="does_catalog_exist",
    description="Checks if a Unity Catalog catalog exists in the Databricks workspace. This is useful for validating that a catalog exists before attempting to create it or perform other operations on it, like giving access or adding a new schema.",
    args_schema=DoesCatalogExistInput
)
async def does_catalog_exist(catalog_name: str) -> Dict[str, Any]:
    """
    Check if catalog exists.
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
        
        # Use SQL to check existence
        # SHOW CATALOGS LIKE 'name' returns a row if it exists
        query = f"SHOW CATALOGS LIKE '{catalog_name}'"
        
        result = await provider.execute_sql(query)
        
        rows = result.get("rows", [])
        exists = len(rows) > 0
        
        return {
            "exists": exists,
            "catalog_name": catalog_name,
            "details": rows[0] if exists else None
        }
        
    except RetryableError as e:
        # Re-raise retryable errors
        raise
    except Exception as e:
        # Wrap others
        raise RetryableError(f"Failed to check catalog existence: {str(e)}")
