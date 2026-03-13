"""
Tool to check if a schema exists.
"""
from typing import Dict, Any
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class DoesSchemaExistInput(BaseModel):
    catalog_name: str = Field(..., description="Name of the catalog")
    schema_name: str = Field(..., description="Name of the schema to check")

@tool(
    name="does_schema_exist",
    description="Checks if a Unity Catalog schema exists in the Databricks workspace within a specific catalog. This is useful for validating that a schema exists before attempting to create it or perform other operations on it.",
    args_schema=DoesSchemaExistInput
)
async def does_schema_exist(catalog_name: str, schema_name: str) -> Dict[str, Any]:
    """
    Check if schema exists.
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
        # SHOW SCHEMAS IN catalog LIKE 'name' returns a row if it exists
        query = f"SHOW SCHEMAS IN `{catalog_name}` LIKE '{schema_name}'"
        
        result = await provider.execute_sql(query)
        
        rows = result.get("rows", [])
        exists = len(rows) > 0
        
        return {
            "exists": exists,
            "catalog_name": catalog_name,
            "schema_name": schema_name,
            "details": rows[0] if exists else None
        }
        
    except RetryableError as e:
        # Re-raise retryable errors
        raise
    except Exception as e:
        # Wrap others
        raise RetryableError(f"Failed to check schema existence: {str(e)}")
