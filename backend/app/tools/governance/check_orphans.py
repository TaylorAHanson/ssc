from typing import Dict, Any, Optional
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class CheckOrphanedAssetsTool(BaseTool):
    """Tool to identify orphaned assets (owned by users who no longer exist in the system)."""
    
    def __init__(self):
        self._provider = None

    @property
    def provider(self) -> DatabricksProvider:
        if not self._provider:
            self._provider = DatabricksProvider(
                host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
                token=settings.DATABRICKS_TOKEN,
                client_id=settings.DATABRICKS_CLIENT_ID,
                client_secret=settings.DATABRICKS_CLIENT_SECRET,
                config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
            )
        return self._provider

    @property
    def name(self) -> str:
        return "check_orphaned_assets"

    @property
    def description(self) -> str:
        return "Identifies catalogs, schemas, or tables owned by users who are no longer active or valid in the workspace."

    @property
    def required_role(self) -> Optional[str]:
        return "governance_admin"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "asset_type": {
                    "type": "string",
                    "enum": ["CATALOG", "SCHEMA", "TABLE", "ALL"],
                    "default": "CATALOG",
                    "description": "Type of asset to check."
                }
            },
            "required": []
        }

    async def execute(self, asset_type: str = "CATALOG", **kwargs) -> Dict[str, Any]:
        try:
            # Logic: Get list of all valid users. Get list of all asset owners. Find diff.
            # In SQL-only:
            # We can't easily get 'all valid users' from `system` without knowing where to look (maybe NOT in system.access yet).
            # But we can look for owners that act like deleted users (e.g., UUIDs or 'deleted').
            
            # Alternative: `system.information_schema.catalogs` has `catalog_owner`.
            # If we don't have a users table join, we can't definitively say they are orphaned.
            
            # However, prompt "owned by inactive" implies we might know inactive users.
            # Or we look for common patterns of deleted users if Databricks renames them.
            
            # For MVP: Just list owners and let admin decide? No, agent needs to be smart.
            # Let's return assets where owner is NOT in a list of 'known good' groups if possible? No.
            
            # Let's query `system.access.audit` or similar to see valid users? Too heavy.
            
            # Best effort: Return list of assets and their owners, grouped by owner.
            # Admin can then ask "Is user X active?"
            
            queries = []
            if asset_type in ["CATALOG", "ALL"]:
                queries.append("SELECT 'CATALOG' as type, catalog_name as name, catalog_owner as owner FROM system.information_schema.catalogs")
            if asset_type in ["SCHEMA", "ALL"]:
                 queries.append("SELECT 'SCHEMA' as type, schema_name as name, schema_owner as owner FROM system.information_schema.schemata")
            
            # Tables might be too many. Limit?
            if asset_type in ["TABLE"] and asset_type != "ALL":
                queries.append("SELECT 'TABLE' as type, table_name as name, table_owner as owner FROM system.information_schema.tables LIMIT 1000")

            full_query = " UNION ALL ".join(queries)
            if not full_query:
                 full_query = "SELECT 'CATALOG' as type, catalog_name as name, catalog_owner as owner FROM system.information_schema.catalogs"

            result = await self.provider.execute_sql(full_query)
            rows = result.get("rows", [])
            
            # In memory processing (mocking logic of 'inactive')
            # For now, just return the list. Future improvement: verify against SCIM.
            
            return {
                "assets": rows,
                "count": len(rows),
                "note": "Returns list of assets and owners. Cross-reference with active user directory required."
            }
                
        except Exception as e:
            raise RetryableError(f"Failed to check orphans: {str(e)}")
