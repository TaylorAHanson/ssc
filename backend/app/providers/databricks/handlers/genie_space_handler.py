import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)

class GenieSpaceResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            spaces = self.workspace_client.genie.spaces.list()
            for space in spaces:
                resources.append({
                    "id": space.id,
                    "name": space.name,
                    "type": "genie_space",
                    "owner": getattr(space, 'creator', 'unknown'),
                    "tags": {t.key: t.value for t in getattr(space, 'tags', [])} if getattr(space, 'tags', None) else {}
                })
        except Exception as e:
            logger.error(f"Failed to discover Genie spaces: {e}")
        return resources
        
    async def kill(self, resource_id: str) -> bool:
        try:
            self.workspace_client.genie.spaces.delete(space_id=resource_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete Genie space {resource_id}: {e}")
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of Genie space {resource_id}: {message}")
        return True
