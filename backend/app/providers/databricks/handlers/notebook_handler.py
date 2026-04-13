import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)

class NotebookResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            # We recursively list notebooks starting from /Users and /Shared
            # This is a simplified version, in reality we'd use workspace.list
            for base_path in ["/Users", "/Shared"]:
                try:
                    for obj in self.workspace_client.workspace.list(base_path, recursive=True):
                        if obj.object_type.value == "NOTEBOOK":
                            resources.append({
                                "id": obj.path,
                                "type": "notebook",
                                "owner": "unknown" # Hard to determine accurately without checking ACLs or inferring from /Users path
                            })
                except Exception as inner_e:
                    logger.warning(f"Could not list notebooks in {base_path}: {inner_e}")
        except Exception as e:
            logger.error(f"Failed to discover Notebooks: {e}")
        return resources
        
    async def kill(self, resource_id: str) -> bool:
        try:
            self.workspace_client.workspace.delete(path=resource_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete Notebook {resource_id}: {e}")
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of Notebook {resource_id}: {message}")
        return True
