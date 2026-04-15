import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)

class JobResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            jobs = self.workspace_client.jobs.list()
            for job in jobs:
                resources.append({
                            "id": str(job.job_id),
                    "name": job.settings.name,
                    "type": "job",
                    "owner": getattr(job, 'creator_user_name', 'unknown'),
                    "tags": dict(job.settings.tags) if hasattr(job, 'settings') and isinstance(getattr(job.settings, 'tags', None), dict) else {getattr(t, 'key', ''): getattr(t, 'value', '') for t in getattr(job.settings, 'tags', [])} if hasattr(job, 'settings') and getattr(job.settings, 'tags', None) else {}
                })
        except Exception as e:
            logger.error(f"Failed to discover jobs: {e}")
        return resources
        
    async def kill(self, resource_id: str) -> bool:
        try:
            self.workspace_client.jobs.delete(job_id=int(resource_id))
            return True
        except Exception as e:
            logger.error(f"Failed to delete job {resource_id}: {e}")
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of job {resource_id}: {message}")
        return True
