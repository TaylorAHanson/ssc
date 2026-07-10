import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)

class ServicePrincipalResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            sps = self.workspace_client.service_principals.list()
            for sp in sps:
                resources.append({
                    "id": sp.id,
                    "name": getattr(sp, 'display_name', getattr(sp, 'application_id', 'unknown')),
                    "type": "service_principal",
                    "active": getattr(sp, 'active', True),
                    "idle_days": 0, # Would need audit logs to determine accurately
                    "tags": {}
                })
        except Exception as e:
            # Re-raise so the Sentinel attributes this to the workspace + classifies
            # it (auth / permission / network) instead of reporting a silent 0.
            logger.error(f"Failed to discover Service Principals: {e}")
            raise
        return resources
        
    async def kill(self, resource_id: str) -> bool:
        try:
            # We deactivate instead of delete for safety
            self.workspace_client.service_principals.patch(
                id=resource_id,
                operations=[{
                    "op": "replace",
                    "path": "active",
                    "value": "False"
                }]
            )
            return True
        except Exception as e:
            logger.error(f"Failed to deactivate Service Principal {resource_id}: {e}")
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of Service Principal {resource_id}: {message}")
        return True
