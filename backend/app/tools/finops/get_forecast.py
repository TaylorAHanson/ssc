from typing import Dict, Any, Optional
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError
from datetime import datetime, timedelta
import calendar

class GetForecastedSpendTool(BaseTool):
    """Tool to forecast spend for the current month."""
    
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
        return "get_forecasted_spend"

    @property
    def description(self) -> str:
        return "Forecasts total spend for the current month based on daily run rate of the last 30 days."

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
                    "format": "date",
                    "description": "Start date for the historical spend calculation (YYYY-MM-DD). Defaults to 30 days ago."
                },
                "end_date": {
                    "type": "string",
                    "format": "date",
                    "description": "End date for the historical spend calculation (YYYY-MM-DD). Defaults to today."
                },
                "forecast_days": {
                    "type": "integer",
                    "description": "Number of historical days to consider for daily run rate calculation. Defaults to 30.",
                    "default": 30
                }
            },
            "required": []
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            # 1. Get current month progress
            now = datetime.utcnow()
            days_in_month = calendar.monthrange(now.year, now.month)[1]
            days_elapsed = now.day
            remaining_days = days_in_month - days_elapsed
            
            # 2. Get spend for last 30 days to calculate daily rate
            start_date = (now - timedelta(days=30)).strftime('%Y-%m-%d')
            end_date = now.strftime('%Y-%m-%d')
            
            query = f"""
                SELECT SUM(u.usage_quantity * lp.pricing.default) as total_cost 
                FROM system.billing.usage u
                JOIN system.billing.list_prices lp 
                  ON u.sku_name = lp.sku_name
                  AND u.usage_start_time >= lp.price_start_time
                  AND (lp.price_end_time IS NULL OR u.usage_end_time <= lp.price_end_time)
                WHERE u.usage_date BETWEEN '{start_date}' AND '{end_date}'
            """
            
            result = await self.provider.execute_sql(query, timeout_seconds=300)
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
            
            result_mtd = await self.provider.execute_sql(query_mtd, timeout_seconds=300)
            rows_mtd = result_mtd.get("rows", [])
            mtd_cost = 0.0
            if rows_mtd and rows_mtd[0].get("total_cost") is not None:
                mtd_cost = float(rows_mtd[0].get("total_cost"))
            
            # 4. Filter logic
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
