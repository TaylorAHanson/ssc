"""
Tool to check object permissions.
"""
from typing import Dict, Any, Literal
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class CheckObjectPermissionsInput(BaseModel):
    object_type: Literal["CATALOG", "SCHEMA", "TABLE", "VOLUME", "FUNCTION", "MODEL"] = Field(..., description="Type of the object (e.g., CATALOG, TABLE)")
    object_name: str = Field(..., description="Full name of the object (e.g., main.default.mytable)")

@tool(
    name="check_object_permissions",
    description="Lists all grants and permissions on a specific Unity Catalog object (catalog, schema, table, etc.).",
    required_role="governance_admin",
    args_schema=CheckObjectPermissionsInput
)
async def check_object_permissions(object_type: str, object_name: str) -> Dict[str, Any]:
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )

        # UC System tables: system.information_schema.table_privileges etc.
        # But SHOW GRANTS is often simpler and supported via SQL execution.
        # However, for agentic use, querying system tables is often more robust if we have access.
        # privileges tables: system.information_schema.catalog_privileges, schema_privileges, table_privileges...
        
        # Let's try to use SQL `SHOW GRANTS` first as it's universal for UC objects mostly.
        # Syntax: SHOW GRANTS ON [TYPE] [NAME]
        
        query = f"SHOW GRANTS ON {object_type} {object_name}"
        
        result = await provider.execute_sql(query, timeout_seconds=300)
        
        # Also get owner
        # DESCRIBE [TYPE] [NAME] usually shows owner? Or use system.information_schema
        # owner = "Unknown" 
        # try:
        #     # Need specific query per type to find owner easily via SQL without parsing text output of DESCRIBE
        #     # Using system.information_schema might be better if we have access.
        #     pass
        # except:
        #     pass

        return {
            "object_type": object_type,
            "object_name": object_name,
            "grants": result.get("rows", []),
            "note": "Output from SHOW GRANTS"
        }
            
    except Exception as e:
        raise RetryableError(f"Failed to check permissions: {str(e)}")
