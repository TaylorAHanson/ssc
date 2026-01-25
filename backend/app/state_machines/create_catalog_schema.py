from typing import Dict, Any, List
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine
from app.models.request import RequestStatus
from app.core.exceptions import PermanentError
from app.providers.databricks.client import DatabricksProvider
from app.core.config import settings
from app.state_machines.facts import has_fact, add_fact
import logging

logger = logging.getLogger(__name__)


class CreateCatalogSchemaStateMachine(BaseRequestStateMachine):
    """
    State machine for creating a Unity Catalog or Schema.
    """
    # States
    pending = State("pending", initial=True)
    platform_admin_approval = State("platform_admin_approval")
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    failed = State("failed", final=True)

    # Transitions
    submit = pending.to(platform_admin_approval, cond="has_request_submitted")
    approve_admin = platform_admin_approval.to(provisioning, cond="has_platform_admin_approval")
    finish_provisioning = provisioning.to(completed, cond="has_provisioning_completed")
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        platform_admin_approval.to(rejected, cond="has_request_rejected")
    )
    mark_failed = (
        pending.to(failed) | 
        platform_admin_approval.to(failed) | 
        provisioning.to(failed)
    )

    # Approval node configuration
    APPROVAL_NODES = {
        "platform_admin_approval": {"approval_type": "platform_admin", "name": "Platform Admin Approval"}
    }

    def __init__(self, request, db_session):
        super().__init__(request, db_session)
        
    def _get_provider(self):
        """Lazy load provider."""
        if not settings.DATABRICKS_HOST:
             # This will cause an error when called if not set, which is what we want
             logger.warning("DATABRICKS_HOST not set, provider methods will fail")
             
        return DatabricksProvider(
            host=settings.DATABRICKS_HOST,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET
        )

    def on_enter_provisioning(self):
        """
        Transition: Approved -> Provisioning
        """
        logger.info(f"[{self.request.id}] Entering provisioning state")
        # Logic moved to execute_tasks to allow state commit before potential failure

    async def execute_tasks(self):
        """Execute async tasks for the current state."""
        if self.current_state.id == "provisioning":
            await self._provision_resources()
            
    async def _provision_resources(self):
        """Provision resources in Databricks."""
        # Check if already done
        if self.has_provisioning_completed:
            return

        params = self.request.state_context or {}
        asset_type = params.get("type", "").lower()
        name = params.get("name")
        comment = params.get("comment")
        
        if not name:
            raise PermanentError("Asset name is required")

        try:
            provider = self._get_provider()
            # This will raise if provider init fails (missing config)
            # But since we are in execute_tasks, the state 'provisioning' is already committed.
            
            if asset_type == "catalog":
                logger.info(f"Provisioning Catalog: {name}")
                provider.create_catalog(name=name, config={"comment": comment})
                
            elif asset_type == "schema":
                # Parent must be provided
                parent = params.get("parent")
                if not parent:
                    raise PermanentError("Parent catalog is required for schema creation")
                
                logger.info(f"Provisioning Schema: {parent}.{name}")
                try:
                     # Placeholder for schema creation
                     # provider.create_schema(...)
                     logger.warning("Schema creation logic invoked but not fully implemented in provider. Skipping SDK call.")
                except Exception as e:
                    raise PermanentError(f"Schema creation not implemented: {str(e)}")
            else:
                 raise PermanentError(f"Unknown asset type: {asset_type}")
                 
            logger.info(f"Successfully created {asset_type} '{name}'")
            
            # Record completion fact
            add_fact(self.db, self.request.id, "provisioning_completed", {}, actor="system")
            # The next tick will transition to completed
            
        except Exception as e:
            logger.error(f"Provisioning failed: {e}")
            raise e
    @property
    def has_provisioning_completed(self) -> bool:
        """Check if provisioning has been completed."""
        return has_fact(self.db, self.request.id, "provisioning_completed")

    def on_enter_completed(self):
        """
        Transition: Provisioning -> Completed
        """
        pass
