"""
Credential Creation state machine.
Creates storage credentials via Databricks API.
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

@workflow(request_types=RequestType.CREDENTIAL_CREATION, feature_flag="core")
class CredentialCreationStateMachine(BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    platform_admin_approval = State("platform_admin_approval")
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    failed = State("failed", final=True)

    submit = pending.to(manager_approval, cond="has_request_submitted")
    approve_manager = manager_approval.to(platform_admin_approval, cond="has_manager_approval")
    approve_admin = platform_admin_approval.to(provisioning, cond="has_platform_admin_approval")
    finish_provisioning = provisioning.to(completed, cond="has_provisioning_completed")
    
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        manager_approval.to(rejected, cond="has_request_rejected") |
        platform_admin_approval.to(rejected, cond="has_request_rejected") |
        provisioning.to(rejected, cond="has_request_rejected")
    )
    
    mark_failed = (
        pending.to(failed) |
        manager_approval.to(failed) |
        platform_admin_approval.to(failed) |
        provisioning.to(failed)
    )

    APPROVAL_NODES = {
        "manager_approval": {"approval_type": "manager", "name": "Manager Approval"},
        "platform_admin_approval": {"approval_type": "platform_admin", "name": "Platform Admin Approval"}
    }
    
    @property
    def has_platform_admin_approval(self) -> bool:
        return has_fact(self.db, self.request.id, "approval_received", approval_type="platform_admin")
        
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
            credential_name = ctx.get("credential_name")
            
            if not credential_name:
                raise PermanentError("credential_name is required")
                
            from app.providers.databricks.client import DatabricksProvider
            provider = DatabricksProvider(
                host=settings.DATABRICKS_HOST,
                token=settings.DATABRICKS_TOKEN,
                client_id=settings.DATABRICKS_CLIENT_ID,
                client_secret=settings.DATABRICKS_CLIENT_SECRET
            )
            
            # TODO: Add actual credential creation logic using Databricks SDK
            logger.info(f"[{self.request.id}] Creating credential: {credential_name}")
            
            add_fact(self.db, self.request.id, "provisioning_completed", {
                "credential_name": credential_name
            }, actor="system")
            
        except Exception as e:
            logger.error(f"[{self.request.id}] Credential creation failed: {e}")
            raise RetryableError(f"Failed to create credential: {e}")
