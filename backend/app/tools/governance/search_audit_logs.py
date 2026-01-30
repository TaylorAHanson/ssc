from typing import Dict, Any, Optional
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class SearchAuditLogsTool(BaseTool):
    """Tool to search system audit logs (system.access.audit)."""
    
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
        return "search_audit_logs"

    @property
    def description(self) -> str:
        return "Searches system.access.audit for specific actions, actors, or targets within a time range."

    @property
    def required_role(self) -> Optional[str]:
        return "security_admin"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format (inclusive)"
                },
                "end_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format (inclusive)"
                },
                "action_name": {
                    "type": "string",
                    "description": "Optional specific action to filter (e.g., 'crreateTable', 'login'). Case insensitive."
                },
                "email": {
                    "type": "string",
                    "description": "Optional email of the actor (user) to filter by."
                }
            },
            "required": ["start_date", "end_date"]
        }

    async def execute(self, start_date: str, end_date: str, action_name: Optional[str] = None, email: Optional[str] = None) -> Dict[str, Any]:
        try:
            # Query system.access.audit
            
            where_clauses = [
                f"event_date BETWEEN '{start_date}' AND '{end_date}'"
            ]
            
            if action_name:
                where_clauses.append(f"lower(action_name) LIKE '%{action_name.lower()}%'")
            
            if email:
                where_clauses.append(f"user_identity.email = '{email}'")
            
            query = f"""
                SELECT 
                    event_time,
                    service_name,
                    action_name,
                    user_identity.email as actor,
                    request_params,
                    response
                FROM system.access.audit
                WHERE {" AND ".join(where_clauses)}
                ORDER BY event_time DESC
                LIMIT 100
            """
            
            result = await self.provider.execute_sql(query)
            
            return {
                "events": result.get("rows", []),
                "count": len(result.get("rows", [])),
                "metadata": {
                    "start_date": start_date,
                    "end_date": end_date
                }
            }
                
        except Exception as e:
            raise RetryableError(f"Failed to search audit logs: {str(e)}")
