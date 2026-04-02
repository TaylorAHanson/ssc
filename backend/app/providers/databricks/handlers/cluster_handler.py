import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)

class ClusterResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            clusters = self.workspace_client.clusters.list()
            for cluster in clusters:
                resources.append({
                    "id": cluster.cluster_id,
                    "name": cluster.cluster_name,
                    "type": "cluster",
                    "owner": cluster.creator_user_name,
                    "state": cluster.state.value
                })
        except Exception as e:
            logger.error(f"Failed to discover clusters: {e}")
        return resources
        
    async def kill(self, resource_id: str) -> bool:
        try:
            # For clusters, we terminate instead of permanent delete, but could make it configurable
            self.workspace_client.clusters.delete(cluster_id=resource_id)
            return True
        except Exception as e:
            logger.error(f"Failed to terminate cluster {resource_id}: {e}")
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of cluster {resource_id}: {message}")
        return True
