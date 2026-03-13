"""
Tool to check if a table exists.
"""
from typing import Dict, Any
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class DoesTableExistInput(BaseModel):
    catalog_name: str = Field(..., description="Name of the catalog")
    schema_name: str = Field(..., description="Name of the schema")
    table_name: str = Field(..., description="Name of the table to check")

@tool(
    name="does_table_exist",
    description="Checks if a Unity Catalog table exists in the Databricks workspace within a specific catalog and schema. This is useful for validating that a table exists before attempting to create it or perform other operations on it.",
    args_schema=DoesTableExistInput
)
async def does_table_exist(catalog_name: str, schema_name: str, table_name: str) -> Dict[str, Any]:
    """
    Check if table exists.
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
        # SHOW TABLES IN catalog.schema LIKE 'name' returns a row if it exists
        query = f"SHOW TABLES IN `{catalog_name}`.`{schema_name}` LIKE '{table_name}'"
        
        result = await provider.execute_sql(query)
        
        rows = result.get("rows", [])
        exists = len(rows) > 0
        
        return {
            "exists": exists,
            "catalog_name": catalog_name,
            "schema_name": schema_name,
            "table_name": table_name,
            "details": rows[0] if exists else None
        }
        
    except RetryableError as e:
        # Re-raise retryable errors
        raise
    except Exception as e:
        # Wrap others
        raise RetryableError(f"Failed to check table existence: {str(e)}")
