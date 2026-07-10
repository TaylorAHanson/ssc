import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)

class GenieSpaceResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            response = self.workspace_client.genie.list_spaces()
            spaces = response.spaces if hasattr(response, 'spaces') and response.spaces is not None else []
            for space in spaces:
                resources.append({
                    "id": getattr(space, 'space_id', getattr(space, 'id', 'unknown')),
                    "name": getattr(space, 'name', getattr(space, 'title', getattr(space, 'space_name', 'unknown'))),
                    "type": "genie_space",
                    "owner": getattr(space, 'creator', 'unknown'),
                    "tags": {t.key: t.value for t in getattr(space, 'tags', [])} if getattr(space, 'tags', None) else {}
                })
        except Exception as e:
            # Re-raise so the Sentinel attributes this to the workspace + classifies
            # it (auth / permission / network) instead of reporting a silent 0.
            logger.error(f"Failed to discover Genie spaces: {e}")
            raise
        return resources
        
    async def kill(self, resource_id: str) -> bool:
        try:
            self.workspace_client.genie.trash_space(space_id=resource_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete Genie space {resource_id}: {e}")
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of Genie space {resource_id}: {message}")
        return True
