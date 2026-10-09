from typing import Dict, Any
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError
import logging

logger = logging.getLogger(__name__)

class FindOwnerInput(BaseModel):
    object_type: str = Field(..., description="Type of object to check. Supported: 'catalog', 'schema', 'table', 'view', 'metric_view', 'job', 'dashboard', 'notebook', 'genie_space'.")
    object_name: str = Field(..., description="Full name (for catalog/schema/table/view/notebook) or ID (for job/dashboard/genie_space) of the object")

@tool(
    name="find_owner",
    description="Look up the owner and metadata tags (e.g. approver_group, access_group) of a Databricks object: catalog, schema, table, view, metric_view, job, dashboard, notebook, or genie_space. Catalog/schema/table lookups use Unity Catalog metadata (BROWSE) and are not a check of the signed-in user's USE CATALOG grant.",
    args_schema=FindOwnerInput
)
async def find_owner(object_type: str, object_name: str) -> Dict[str, Any]:
    """
    Finds the owner and relevant tags of a Databricks object.

    Unity Catalog catalog/schema/table/view lookups read
    ``system.information_schema`` (BROWSE), not the SDK ``*.get`` APIs (those
    require USE CATALOG and would report the *app* identity's grants, not
    whether the signed-in user can see the object in Catalog Explorer).
    Jobs, dashboards, notebooks, and genie spaces still use the workspace SDK.

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
