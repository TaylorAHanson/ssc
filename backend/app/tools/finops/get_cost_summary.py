from typing import Dict, Any, Optional
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class GetCostSummaryTool(BaseTool):
    """Tool to retrieve aggregated cost data."""
    
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
        return "get_cost_summary"

    @property
    def description(self) -> str:
        return "Retrieves aggregated cost data over a specified time range from system.billing.usage. Can group by workspace, SKU, user, or tags."

    @property
    def required_role(self) -> Optional[str]:
        return "finance_admin"

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
                "granularity": {
                    "type": "string",
                    "enum": ["daily", "monthly", "total"],
                    "default": "total",
                    "description": "Time granularity for the result"
                },
                "group_by": {
                    "type": "string",
                    "description": "Dimension to group by: 'workspace_id', 'sku_name', 'usage_type', or 'custom_tags.[key]'"
                }
            },
            "required": ["start_date", "end_date"]
        }

    async def execute(self, start_date: str, end_date: str, granularity: str = "total", group_by: Optional[str] = None) -> Dict[str, Any]:
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            # Build query
            # Join with list_prices to get the cost
            select_clause = "SUM(u.usage_quantity * lp.pricing.default) as total_cost"
            group_clause = ""
            order_clause = "ORDER BY total_cost DESC"
            
            cols = []
            
            if granularity == "daily":
                cols.append("u.usage_date")
            elif granularity == "monthly":
                cols.append("DATE_TRUNC('MONTH', u.usage_date) as month")
            
            if group_by:
                # Handle group by alias properly
                if "sku_name" in group_by:
                    cols.append("u.sku_name")
                elif "workspace_id" in group_by:
                    cols.append("u.workspace_id")
                else:
                    cols.append(group_by) # hope for the best on other columns
            
            if cols:
                select_clause = f"{', '.join(cols)}, {select_clause}"
                group_clause = f"GROUP BY {', '.join([str(i+1) for i in range(len(cols))])}"
                order_clause = f"ORDER BY 1 ASC" if granularity in ["daily", "monthly"] else order_clause
            
            query = f"""
                SELECT {select_clause}
                FROM system.billing.usage u
                JOIN system.billing.list_prices lp 
                  ON u.sku_name = lp.sku_name
                  AND u.usage_start_time >= lp.price_start_time
                  AND (lp.price_end_time IS NULL OR u.usage_end_time <= lp.price_end_time)
                WHERE u.usage_date BETWEEN '{start_date}' AND '{end_date}'
                {group_clause}
                {order_clause}
                LIMIT 1000
            """
            
            logger.info(f"Executing Cost SQL: {query}")
            
            result = await self.provider.execute_sql(query)
            rows = result.get("rows", [])
            logger.info(f"Cost SQL Result: {len(rows)} rows returned")
            
            # If no rows, try to give a hint
            warning = None
            if not rows:
                warning = "No cost data found for this date range. Check if system.billing.usage is populated."

            return {
                "costs": rows,
                "currency": "USD", # Assumption
                "metadata": {
                    "start_date": start_date,
                    "end_date": end_date,
                    "granularity": granularity,
                    "group_by": group_by,
                    "warning": warning
                }
            }
        except Exception as e:
            logger.error(f"Cost Tool Error: {e}")
            raise RetryableError(f"Failed to get cost summary: {str(e)}")
