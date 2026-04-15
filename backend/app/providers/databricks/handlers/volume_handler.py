import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)

class VolumeResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            # Requires Unity Catalog catalogs.list() -> schemas.list() -> volumes.list()
            # Simplified for now to just show the API usage
            catalogs = self.workspace_client.catalogs.list()
            for catalog in catalogs:
                schemas = self.workspace_client.schemas.list(catalog_name=catalog.name)
                for schema in schemas:
                    volumes = self.workspace_client.volumes.list(catalog_name=catalog.name, schema_name=schema.name)
                    for volume in volumes:
                        resources.append({
                            "id": volume.full_name,
                            "type": "storage",
                            "storage_type": "volume",
                            "owner": volume.owner,
                            "tags": {}
                        })
        except Exception as e:
            logger.error(f"Failed to discover Volumes: {e}")
        return resources
        
    async def kill(self, resource_id: str) -> bool:
        try:
            self.workspace_client.volumes.delete(name=resource_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete Volume {resource_id}: {e}")
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of Volume {resource_id}: {message}")
        return True
