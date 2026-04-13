import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)

class SqlWarehouseResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            warehouses = self.workspace_client.sql.endpoints.list()
            for warehouse in warehouses:
                resources.append({
                    "id": warehouse.id,
                    "name": warehouse.name,
                    "type": "sql_warehouse",
                    "owner": warehouse.creator_name,
                    "state": warehouse.state.value,
                    "policy_id": warehouse.policy_id,
                    "tags": {t.key: t.value for t in (warehouse.tags.custom_tags if warehouse.tags else [])}
                })
        except Exception as e:
            logger.error(f"Failed to discover SQL warehouses: {e}")
        return resources
        
    async def kill(self, resource_id: str) -> bool:
        try:
            self.workspace_client.sql.endpoints.delete(id=resource_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete SQL warehouse {resource_id}: {e}")
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of SQL warehouse {resource_id}: {message}")
        return True
