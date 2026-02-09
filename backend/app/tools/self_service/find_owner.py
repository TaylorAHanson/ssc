from typing import Dict, Any
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

    IMPLEMENTATION APPROACH:
    This tool uses the DatabricksProvider.find_object_owner() method which leverages
    the Databricks SDK API calls (client.catalogs.get(), client.schemas.get(), etc.)
    to retrieve object metadata including the owner.

    ALTERNATIVE APPROACH:
    The DatabricksProvider also provides get_asset_owner() method which uses
    'DESCRIBE EXTENDED' SQL commands to fetch owner information. This approach is
    currently used by the data access state machine.

    When to use which approach:
    - SDK API approach (this tool):
      * Faster and more reliable for Unity Catalog objects
      * Works for catalogs, schemas, tables, jobs, dashboards, notebooks, genie spaces
      * Returns structured metadata directly from the SDK
      * Used by Agent tools for interactive queries

    - DESCRIBE EXTENDED approach (get_asset_owner):
      * Uses SQL warehouse execution
      * Works for catalogs, schemas, tables, and volumes
      * Useful when you need to query via SQL or when SDK access is limited
      * Currently used by state machine for data owner approval flow

    Both approaches are maintained for flexibility and different use cases.

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

        # Use provider method
        return await provider.find_object_owner(object_type, object_name)

    except RetryableError as e:
        raise
    except Exception as e:
        return {
            "found": False,
            "message": f"Failed to find owner for {object_type} '{object_name}': {str(e)}",
            "object_type": object_type,
            "object_name": object_name
        }
