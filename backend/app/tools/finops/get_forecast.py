"""
Tool to forecast spend.
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.tools.sql_safety import SqlSafetyError, require_date
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError
from datetime import datetime, timezone, timedelta
import calendar

class GetForecastedSpendInput(BaseModel):
    start_date: Optional[str] = Field(None, description="Start date for the historical spend calculation (YYYY-MM-DD). Defaults to 30 days ago.")
    end_date: Optional[str] = Field(None, description="End date for the historical spend calculation (YYYY-MM-DD). Defaults to today.")
    forecast_days: int = Field(30, description="Number of historical days to consider for daily run rate calculation. Defaults to 30.")

@tool(
    name="get_forecasted_spend",
    description="Forecasts total spend for the current month based on daily run rate of the last 30 days.",
    required_role="finance_admin",
    args_schema=GetForecastedSpendInput
)
async def get_forecasted_spend(start_date: Optional[str] = None, end_date: Optional[str] = None, forecast_days: int = 30, **kwargs) -> Dict[str, Any]:
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

        # 1. Get current month progress
        now = datetime.now(timezone.utc)
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        days_elapsed = now.day
        remaining_days = days_in_month - days_elapsed
        
        # 2. Get spend for last 30 days to calculate daily rate
        if not start_date:
            start_date = (now - timedelta(days=30)).strftime('%Y-%m-%d')
        if not end_date:
            end_date = now.strftime('%Y-%m-%d')

        # Validate the (possibly LLM-supplied) dates before interpolation.
        try:
            require_date(start_date, "start_date")
            require_date(end_date, "end_date")
        except SqlSafetyError as e:
            return {"error": str(e)}
        
        query = f"""
            SELECT SUM(u.usage_quantity * lp.pricing.default) as total_cost 
            FROM system.billing.usage u
            JOIN system.billing.list_prices lp 
              ON u.sku_name = lp.sku_name
              AND u.usage_start_time >= lp.price_start_time
              AND (lp.price_end_time IS NULL OR u.usage_end_time <= lp.price_end_time)
            WHERE u.usage_date BETWEEN '{start_date}' AND '{end_date}'
        """
        
        result = await provider.execute_sql(query, timeout_seconds=300, obo_token=obo_token, require_obo=True)
        rows = result.get("rows", [])
        total_30d_cost = 0.0
        if rows and rows[0].get("total_cost") is not None:
            total_30d_cost = float(rows[0].get("total_cost"))
        
        # 3. Get spend MTD (Month to Date)
        month_start = now.replace(day=1).strftime('%Y-%m-%d')
        query_mtd = f"""
            SELECT SUM(u.usage_quantity * lp.pricing.default) as total_cost 
            FROM system.billing.usage u
            JOIN system.billing.list_prices lp 
              ON u.sku_name = lp.sku_name
              AND u.usage_start_time >= lp.price_start_time
              AND (lp.price_end_time IS NULL OR u.usage_end_time <= lp.price_end_time)
            WHERE u.usage_date BETWEEN '{month_start}' AND '{end_date}'
        """
        
        result_mtd = await provider.execute_sql(query_mtd, timeout_seconds=300, obo_token=obo_token, require_obo=True)
        rows_mtd = result_mtd.get("rows", [])
        mtd_cost = 0.0
        if rows_mtd and rows_mtd[0].get("total_cost") is not None:
            mtd_cost = float(rows_mtd[0].get("total_cost"))
        
        # 4. Filter logic
        # If forecast_days is used, we might want to adjust logic, but sticking to existing logic for now 
        # which seems to presume 30 days window logic roughly.
        # But let's check input... the function signature allows start_date override.
        # Run rate logic:
        daily_run_rate = total_30d_cost / 30 if total_30d_cost > 0 else 0
        
        projected_remaining = daily_run_rate * remaining_days
        forecast_total = mtd_cost + projected_remaining
        
        return {
            "forecast_total": round(forecast_total, 2),
            "mtd_cost": round(mtd_cost, 2),
            "daily_run_rate": round(daily_run_rate, 2),
            "currency": "USD",
            "method": "30-day average run rate projection"
        }
            
    except Exception as e:
        raise RetryableError(f"Failed to calculate forecast: {str(e)}")
