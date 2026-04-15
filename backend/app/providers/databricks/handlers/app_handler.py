import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)

class AppResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            # Note: Requires databricks-sdk >= 0.20.0 for apps
            apps = self.workspace_client.apps.list()
            for app in apps:
                resources.append({
                    "id": app.name,
                    "type": "app",
                    "owner": getattr(app, 'creator', 'unknown'),
                    "state": getattr(app.active_deployment, 'state', 'UNKNOWN') if getattr(app, 'active_deployment', None) else 'UNKNOWN',
                    "tags": {}
                })
        except Exception as e:
            logger.error(f"Failed to discover apps: {e}")
        return resources
        
    async def kill(self, resource_id: str) -> bool:
        try:
            self.workspace_client.apps.delete(name=resource_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete app {resource_id}: {e}")
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of app {resource_id}: {message}")
        return True
