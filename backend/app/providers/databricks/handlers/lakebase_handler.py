import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)


class LakebaseResourceHandler(BaseResourceHandler):
    """Discovery + remediation for Lakebase (managed Postgres) database instances.

    Mirrors the app/genie handlers so the Enforcement Sentinel can require these
    to be on the allowlist in enterprise prod (see policies/lakebase.rego).
    """

    async def discover(self) -> List[Dict[str, Any]]:
        resources: List[Dict[str, Any]] = []
        try:
            instances = self.workspace_client.database.list_database_instances()
            for inst in instances:
                state = getattr(inst, "state", None)
                resources.append({
                    # Database instances are addressed by name (the same handle
                    # delete_database_instance takes) — used as the allowlist id.
                    "id": getattr(inst, "name", getattr(inst, "uid", "unknown")),
                    "name": getattr(inst, "name", "unknown"),
                    "type": "lakebase",
                    "owner": getattr(inst, "creator", "unknown"),
                    "state": getattr(state, "value", state) if state is not None else "UNKNOWN",
                    "stopped": bool(getattr(inst, "stopped", False)),
                    "tags": dict(getattr(inst, "custom_tags", None) or {}),
                })
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to discover Lakebase database instances: {e}")
        return resources

    async def kill(self, resource_id: str) -> bool:
        try:
            self.workspace_client.database.delete_database_instance(name=resource_id)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to delete Lakebase database instance {resource_id}: {e}")
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of Lakebase database instance {resource_id}: {message}")
        return True
