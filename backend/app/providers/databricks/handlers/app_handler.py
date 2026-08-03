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
                    # `app.name` is the human-readable slug and also the id the
                    # delete/kill API takes, so both fields intentionally use it.
                    "name": getattr(app, 'display_name', None) or app.name,
                    "type": "app",
                    "owner": getattr(app, 'creator', 'unknown'),
                    "state": getattr(app.active_deployment, 'state', 'UNKNOWN') if getattr(app, 'active_deployment', None) else 'UNKNOWN',
                    "tags": {}
                })
        except Exception as e:
            # Re-raise so the Sentinel attributes this to the workspace + classifies
            # it (auth / permission / network) instead of reporting a silent 0.
            logger.error(f"Failed to discover apps: {e}")
            raise
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
