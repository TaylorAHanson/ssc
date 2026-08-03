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
                space_id = getattr(space, 'space_id', None) or getattr(space, 'id', None) or 'unknown'
                # GenieSpace exposes the display name as `title`; `name` /
                # `space_name` are only here for older/other SDK shapes. Fall
                # back to the id rather than the literal string "unknown", which
                # would render as the name of every space the SDK surprises us with.
                resources.append({
                    "id": space_id,
                    "name": (
                        getattr(space, 'title', None)
                        or getattr(space, 'name', None)
                        or getattr(space, 'space_name', None)
                        or space_id
                    ),
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
