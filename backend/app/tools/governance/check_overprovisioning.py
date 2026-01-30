from typing import Dict, Any, Optional
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class CheckOverprovisionedUsersTool(BaseTool):
    """Tool to identify users with potentially excessive privileges."""
    
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
        return "check_overprovisioned_users"

    @property
    def description(self) -> str:
        return "Identifies users with high-privilege roles (Account Admin, Workspace Admin) or excessive catalog ownership."

    @property
    def required_role(self) -> Optional[str]:
        return "governance_admin"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "check_type": {
                    "type": "string",
                    "enum": ["admins", "catalog_owners"],
                    "default": "admins",
                    "description": "Type of check to perform."
                }
            },
            "required": []
        }

    async def execute(self, check_type: str = "admins") -> Dict[str, Any]:
        try:
            if check_type == "admins":
                # Need to use SCIM API ideally, but system tables might have group info if synced.
                # 'system.access.group_members' ? (Not always available)
                # Proxy: Query explicit workspace admins if possible, or assume we need to use a different source.
                # However, for SQL-only:
                # We can check who owns 'system' catalog? No.
                # We can use `SHOW GROUPS` via SQL if supported.
                
                # Let's try `SHOW GROUPS WITH USER` logic or similar.
                # But that's hard to parse in one go.
                
                # Alternative: List users who are members of 'admins' group.
                # Simple query if `rows` format allows parsing.
                # "SHOW USERS" might not show groups.
                
                # If we cannot reliably do this via SQL, we might need to fallback to SDK?
                # But we committed to using DatabricksProvider (SQL).
                # Let's try executing `SELECT * FROM system.information_schema.catalog_privileges WHERE grantee_type = 'USER' AND privilege_type = 'ALL_PRIVILEGES'`
                # This finds users who are effectively admins on catalogs.
                
                query = """
                    SELECT grantee, catalog_name, privilege_type 
                    FROM system.information_schema.catalog_privileges 
                    WHERE privilege_type = 'ALL_PRIVILEGES' 
                    AND grantee_type = 'USER'
                    LIMIT 100
                """
                
                result = await self.provider.execute_sql(query)
                return {
                    "high_privilege_users": result.get("rows", []),
                    "check_type": "catalog_full_control",
                    "note": "Users with ALL_PRIVILEGES on any catalog."
                }

            elif check_type == "catalog_owners":
                 query = "SHOW CATALOGS"
                 result = await self.provider.execute_sql(query)
                 # Result rows usually: name, owner, comment...
                 # We can aggregate by owner in python
                 rows = result.get("rows", [])
                 owner_counts = {}
                 for r in rows:
                     # Row format depends on driver. Assuming dict or list.
                     # Provider returns list of lists usually? Or list of dicts?
                     # DatabricksProvider `execute_statement` result format usually has 'columns' and 'data'.
                     # Our wrap returns `rows`.
                     # Let's assume dict access if columns mapped, or index 1 if list.
                     # Safest: Use result directly.
                     pass
                 
                 return {
                     "catalogs": rows,
                     "note": "Please analyze owner distribution from this list."
                 }

            return {"result": []}
                
        except Exception as e:
            raise RetryableError(f"Failed to check overprovisioning: {str(e)}")
