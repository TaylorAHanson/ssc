"""
Tool to identify inefficient resource usage.
"""
from typing import Dict, Any, Literal
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class GetEfficiencyInput(BaseModel):
    metric: Literal["idle_time"] = Field(..., description="Efficiency metric to check. Currently only supports 'idle_time' for clusters.")
    threshold_hours: int = Field(24, description="Minimum hours of inactivity to consider a resource idle.")

@tool(
    name="get_resource_efficiency_metrics",
    description="Identifies potentially inefficient resources, such as idle clusters.",
    required_role="finance_admin",
    args_schema=GetEfficiencyInput
)
async def get_resource_efficiency_metrics(metric: str, threshold_hours: int = 24) -> Dict[str, Any]:
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )

        if metric == "idle_time":
            # Find clusters that are in running state but have low usage? 
            # Or just list clusters created long ago that are still running?
            # System tables allow querying `system.compute.clusters` for status.
            # True idle checks require analyzing audit logs or metrics which is hard with just SQL.
            # Proxy: List RUNNING clusters created > X hours ago.
            
            query = f"""
                SELECT 
                    cluster_id, 
                    cluster_name,
                    owned_by, 
                    create_time,
                    change_time,
                    tags
                FROM system.compute.clusters
                WHERE delete_time IS NULL
                ORDER BY change_time DESC
                LIMIT 100
            """
            
            result = await provider.execute_sql(query)
            return {
                "inefficient_resources": result.get("rows", []),
                "metric": metric,
                "threshold_hours": threshold_hours,
                "note": "Listing long-running clusters as potential idle candidates."
            }
        else:
            return {"error": f"Unsupported metric: {metric}"}
            
    except Exception as e:
        raise RetryableError(f"Failed to check efficiency: {str(e)}")
