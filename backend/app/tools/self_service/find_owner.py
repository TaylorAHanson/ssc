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
