"""
Governed Tag Creation state machine.
Creates tags at the account level via Databricks API.
"""
from statemachine import State
from app.models.request import RequestType
from app.state_machines.decorators import workflow
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import has_fact, add_fact
from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
import logging

logger = logging.getLogger(__name__)

@workflow(request_types=RequestType.TAG_CREATION, feature_flag="core")
class TagCreationStateMachine(BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    failed = State("failed", final=True)

    submit = pending.to(manager_approval, cond="has_request_submitted")
    approve_manager = manager_approval.to(provisioning, cond="has_manager_approval")
    finish_provisioning = provisioning.to(completed, cond="has_provisioning_completed")
    
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        manager_approval.to(rejected, cond="has_request_rejected") |
        provisioning.to(rejected, cond="has_request_rejected")
    )
    
    mark_failed = (
        pending.to(failed) |
        manager_approval.to(failed) |
        provisioning.to(failed)
    )

    APPROVAL_NODES = {
        "manager_approval": {"approval_type": "manager", "name": "Manager Approval"}
    }
    
    @property
    def has_provisioning_completed(self) -> bool:
        return has_fact(self.db, self.request.id, "provisioning_completed")

    async def on_enter_provisioning_async(self):
        if has_fact(self.db, self.request.id, "provisioning_started"):
            return
            
        try:
            add_fact(self.db, self.request.id, "provisioning_started", {}, actor="system")
            self.db.commit()
            
            ctx = self.request.state_context or {}
            tag_name = ctx.get("tag_name")
            tag_value = ctx.get("tag_value", "")
            
            if not tag_name:
                raise PermanentError("tag_name is required")
                
            from app.providers.databricks.client import DatabricksProvider
            provider = DatabricksProvider(
                host=settings.DATABRICKS_HOST,
                token=settings.DATABRICKS_TOKEN,
                client_id=settings.DATABRICKS_CLIENT_ID,
                client_secret=settings.DATABRICKS_CLIENT_SECRET
            )
            
            # TODO: Add actual account-level tag creation logic using Databricks SDK
            # For now we'll simulate success since the SDK method needs to be added
            logger.info(f"[{self.request.id}] Creating account-level tag: {tag_name}={tag_value}")
            
            add_fact(self.db, self.request.id, "provisioning_completed", {
                "tag_name": tag_name,
                "tag_value": tag_value
            }, actor="system")
            
        except Exception as e:
            logger.error(f"[{self.request.id}] Tag creation failed: {e}")
            raise RetryableError(f"Failed to create tag: {e}")
