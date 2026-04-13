import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)

class DashboardResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            dashboards = self.workspace_client.lakeview.list()
            for dash in dashboards:
                resources.append({
                    "id": dash.dashboard_id,
                    "name": dash.display_name,
                    "type": "dashboard",
                    # Mocking some fields since list might not have full details
                    "owner": getattr(dash, 'creator_user_name', 'unknown'),
                    "uses_embedded_credentials": getattr(dash, 'uses_embedded_credentials', False),
                    "shared_with": getattr(dash, 'shared_with', [])
                })
        except Exception as e:
            logger.error(f"Failed to discover dashboards: {e}")
        return resources
        
    async def kill(self, resource_id: str) -> bool:
        try:
            self.workspace_client.lakeview.trash(dashboard_id=resource_id)
            return True
        except Exception as e:
            logger.error(f"Failed to trash dashboard {resource_id}: {e}")
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of dashboard {resource_id}: {message}")
        return True
