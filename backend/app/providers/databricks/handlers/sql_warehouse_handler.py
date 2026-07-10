import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)

class SqlWarehouseResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            warehouses = self.workspace_client.warehouses.list()
            for warehouse in warehouses:
                resources.append({
                    "id": warehouse.id,
                    "name": warehouse.name,
                    "type": "sql_warehouse",
                    "owner": warehouse.creator_name,
                    "state": warehouse.state.value if hasattr(warehouse.state, 'value') else str(warehouse.state),
                    "policy_id": getattr(warehouse, 'policy_id', None) if hasattr(warehouse, 'policy_id') else None,
                    "tags": {t.key: t.value for t in (warehouse.tags.custom_tags if hasattr(warehouse, 'tags') and warehouse.tags and hasattr(warehouse.tags, 'custom_tags') else [])}
                })
        except Exception as e:
            # Re-raise so the Sentinel attributes this to the workspace + classifies
            # it (auth / permission / network) instead of reporting a silent 0.
            logger.error(f"Failed to discover SQL warehouses: {e}")
            raise
        return resources
        
    async def kill(self, resource_id: str) -> bool:
        try:
            self.workspace_client.warehouses.delete(id=resource_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete SQL warehouse {resource_id}: {e}")
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of SQL warehouse {resource_id}: {message}")
        return True
