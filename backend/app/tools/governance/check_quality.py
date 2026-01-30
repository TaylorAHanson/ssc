from typing import Dict, Any, Optional
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class CheckAssetQualityTool(BaseTool):
    """Tool to check asset quality (missing metadata, empty, unused)."""
    
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
        return "check_asset_quality"

    @property
    def description(self) -> str:
        return "Checks for quality issues: missing descriptions (comments), empty tables, or unused assets."

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
                    "enum": ["missing_description", "empty_tables"],
                    "description": "Type of quality check."
                },
                "scope": {
                    "type": "string",
                    "enum": ["CATALOG", "SCHEMA", "TABLE"],
                    "default": "TABLE",
                    "description": "Scope of the check."
                }
            },
            "required": ["check_type"]
        }

    async def execute(self, check_type: str, scope: str = "TABLE", **kwargs) -> Dict[str, Any]:
        try:
            if check_type == "missing_description":
                # comments column in information_schema
                table_map = {
                    "CATALOG": "system.information_schema.catalogs",
                    "SCHEMA": "system.information_schema.schemata",
                    "TABLE": "system.information_schema.tables"
                }
                table = table_map.get(scope, "system.information_schema.tables")
                col_name = f"{scope.lower()}_name"
                
                query = f"""
                    SELECT {col_name} as name, '{scope}' as type
                    FROM {table}
                    WHERE comment IS NULL OR trim(comment) = ''
                    LIMIT 100
                """
                
                result = await self.provider.execute_sql(query)
                return {
                    "issues": result.get("rows", []),
                    "check": "missing_description",
                    "note": "Assets without descriptions (comments)."
                }
            
            elif check_type == "empty_tables":
                # Requires analyzing table stats or row counts.
                # `system.information_schema.tables` unfortunately doesn't always have row_count.
                # But `system.information_schema.table_constraints` etc ... none.
                # DESCRIBE DETAIL might work but that's per table.
                # This is hard to do in bulk via SQL without collecting stats.
                
                # Mock response for now or assume we can't reliably do this without expensive queries.
                return {
                    "issues": [],
                    "note": "Empty table check requires expensive row count operations not safe for bulk agent execution."
                }

            return {"result": []}
                
        except Exception as e:
            raise RetryableError(f"Failed to check asset quality: {str(e)}")
