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
        return """Searches system.access.audit for specific actions, actors, or targets.
Supports aggregations like COUNT and GROUP BY to answer analytical questions.

Table Schema for system.access.audit:
- account_id (string): ID of the account
- workspace_id (string): ID of the workspace
- version (string): Audit log schema version (typically 2.0)
- event_time (timestamp): Timestamp of the event (UTC)
- event_date (date): Calendar date the action took place
- source_ip_address (string): IP address where the request originated
- user_agent (string): Origination of request (client info)
- session_id (string): ID of the session
- user_identity (struct): Identity of user initiating request (e.g. {"email": "user@domain.com"})
- service_name (string): Service name initiating request (e.g. unityCatalog, clusters, sqlConnector)
- action_name (string): Category of the event (e.g. getTable, login, createCluster)
- request_id (string): Unique ID of request
- request_params (map): Map of key values containing request parameters
- response (struct): Struct containing statusCode (e.g. 200) and errorMessage
- audit_level (string): 'WORKSPACE' or 'ACCOUNT'
- event_id (string): Unique ID of the event
- identity_metadata (struct): Identities involved (run_by, run_as)
"""

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
                    "description": "Optional specific action to filter (e.g., 'login'). Case insensitive."
                },
                "email": {
                    "type": "string",
                    "description": "Optional email of the actor to filter by."
                },
                "aggregation_type": {
                    "type": "string",
                    "enum": ["count", "list"],
                    "description": "Whether to return a count of events or a list of individual event details (default: 'list')"
                },
                "group_by_columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of columns to group by (e.g., ['action_name', 'user_identity.email']). Only used if aggregation_type is 'count'."
                },
                "additional_where": {
                    "type": "string",
                    "description": "Optional raw SQL WHERE clause snippet for advanced filtering (e.g., \"response.statusCode = '200'\")"
                }
            },
            "required": ["start_date", "end_date"]
        }

    async def execute(
        self, 
        start_date: str, 
        end_date: str, 
        action_name: Optional[str] = None, 
        email: Optional[str] = None,
        aggregation_type: str = "list",
        group_by_columns: Optional[list] = None,
        additional_where: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        try:
            where_clauses = [
                f"event_date BETWEEN '{start_date}' AND '{end_date}'"
            ]
            
            if action_name:
                where_clauses.append(f"lower(action_name) LIKE '%{action_name.lower()}%'")
            
            if email:
                where_clauses.append(f"user_identity.email = '{email}'")
            
            if additional_where:
                where_clauses.append(additional_where)
            
            where_str = " AND ".join(where_clauses)
            
            if aggregation_type == "count":
                if group_by_columns:
                    group_str = ", ".join(group_by_columns)
                    query = f"""
                        SELECT {group_str}, COUNT(*) as event_count
                        FROM system.access.audit
                        WHERE {where_str}
                        GROUP BY {group_str}
                        ORDER BY event_count DESC
                        LIMIT 100
                    """
                else:
                    query = f"SELECT COUNT(*) as event_count FROM system.access.audit WHERE {where_str}"
            else:
                # Default list mode
                query = f"""
                    SELECT 
                        event_time,
                        service_name,
                        action_name,
                        user_identity.email as actor,
                        request_params,
                        response
                    FROM system.access.audit
                    WHERE {where_str}
                    ORDER BY event_time DESC
                    LIMIT 100
                """
            
            result = await self.provider.execute_sql(query, timeout_seconds=300)
            
            return {
                "results": result.get("rows", []),
                "query_type": aggregation_type,
                "metadata": {
                    "start_date": start_date,
                    "end_date": end_date,
                    "query": query
                }
            }
                
        except Exception as e:
            raise RetryableError(f"Failed to search audit logs: {str(e)}")
