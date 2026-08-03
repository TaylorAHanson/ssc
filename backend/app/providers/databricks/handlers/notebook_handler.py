import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)

class NotebookResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        base_paths = ["/Users", "/Shared"]
        path_errors = []
        try:
            # We recursively list notebooks starting from /Users and /Shared
            # This is a simplified version, in reality we'd use workspace.list
            for base_path in base_paths:
                try:
                    for obj in self.workspace_client.workspace.list(base_path, recursive=True):
                        # ``object_type`` can be None for some workspace entries; a raw
                        # ``.value`` access there throws 'NoneType' has no attribute
                        # 'value' and aborts the whole listing (both paths fail => the
                        # handler reports a systemic failure and the scan goes partial).
                        # Read it defensively and skip anything that isn't a notebook.
                        obj_type = getattr(getattr(obj, "object_type", None), "value", None)
                        if obj_type == "NOTEBOOK":
                            resources.append({
                            "id": obj.path,
                            # Workspace objects have no display name; the path
                            # basename is what people call the notebook, with the
                            # full path still available as the id.
                            "name": obj.path.rsplit("/", 1)[-1] or obj.path,
                            "type": "notebook",
                            "owner": "unknown", # Hard to determine accurately without checking ACLs or inferring from /Users path
                            "tags": {}
                            })
                except Exception as inner_e:
                    logger.warning(f"Could not list notebooks in {base_path}: {inner_e}")
                    path_errors.append(inner_e)
            # If EVERY base path failed, this is a systemic failure (auth / network /
            # permission), not a per-path quirk. Propagate it so the Sentinel can
            # attribute + classify it rather than reporting a silent 0 notebooks.
            if path_errors and len(path_errors) == len(base_paths):
                raise path_errors[0]
        except Exception as e:
            logger.error(f"Failed to discover Notebooks: {e}")
            raise
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
