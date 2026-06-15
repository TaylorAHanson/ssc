"""
Tool to retrieve cost summary.
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.tools.sql_safety import SqlSafetyError, reject_dangerous_snippet, require_date
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError
import logging

class GetCostSummaryInput(BaseModel):
    start_date: str = Field(..., description="Start date in YYYY-MM-DD format (inclusive)")
    end_date: str = Field(..., description="End date in YYYY-MM-DD format (inclusive)")
    granularity: str = Field("total", description="Time granularity for the result: 'daily', 'monthly', 'total'")
    group_by: Optional[str] = Field(None, description="Dimension to group by: 'workspace_id', 'sku_name', 'usage_type', or 'custom_tags.[key]'")

@tool(
    name="get_cost_summary",
    description="Retrieves aggregated cost data over a specified time range from system.billing.usage. Can group by workspace, SKU, user, or tags.",
    required_role="finance_admin",
    args_schema=GetCostSummaryInput
)
async def get_cost_summary(start_date: str, end_date: str, granularity: str = "total", group_by: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    logger = logging.getLogger(__name__)

    # Validate interpolated values (dates + the free-form group_by dimension).
    try:
        require_date(start_date, "start_date")
        require_date(end_date, "end_date")
        if group_by:
            reject_dangerous_snippet(group_by, "group_by")
            if any(ch in group_by for ch in (",", "(", ")")):
                raise SqlSafetyError("group_by must be a single column/tag dimension")
    except SqlSafetyError as e:
        return {"error": str(e)}

    try:
        # Read-only billing query runs as the calling user (OBO) when available;
        # falls back to the service principal otherwise.
        obo_token = kwargs.get("_obo_token")
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )

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
        
        # Developer decision: Set timeout to 300s (5 mins) for heavy cost queries
        result = await provider.execute_sql(query, timeout_seconds=300, obo_token=obo_token, require_obo=True)
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
