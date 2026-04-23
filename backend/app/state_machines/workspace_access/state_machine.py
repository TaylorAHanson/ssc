"""
Workspace Access state machine.
"""
from statemachine import State
from app.models.request import RequestType
from app.state_machines.decorators import workflow
from app.state_machines.base import BaseRequestStateMachine


@workflow(request_types=RequestType.WORKSPACE_ACCESS, feature_flag="core")
class WorkspaceAccessStateMachine(BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)

    submit_domain = pending.to(manager_approval, cond=["has_request_submitted", "requires_manager_approval"])
    submit_enterprise = pending.to(provisioning, cond=["has_request_submitted", "is_auto_approve"])
    approve_manager = manager_approval.to(provisioning, cond="has_manager_approval")
    finish_provisioning = provisioning.to(completed, cond="has_access_granted")
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        manager_approval.to(rejected, cond="has_request_rejected")
    )
    
    # Approval node configuration
    APPROVAL_NODES = {
        "manager_approval": {"approval_type": "manager", "name": "Manager Approval", "assignee_context_key": "manager_email"}
    }

    STATE_COMPLETION_FACTS = {
        **BaseRequestStateMachine.STATE_COMPLETION_FACTS,
        "provisioning": "access_granted",
    }

    STATE_LOG_FACTS = {
        **BaseRequestStateMachine.STATE_LOG_FACTS,
        "provisioning": ["access_granted"],
    }

    @property
    def is_auto_approve(self) -> bool:
        """Check if the workspace access request is auto-approved (Enterprise Prod)."""
        ctx = self.request.state_context or {}
        ws_type = ctx.get("workspace_type", "").lower()
        env = ctx.get("environment", "").lower()
        return ws_type == "enterprise" and env == "prd"

    @property
    def requires_manager_approval(self) -> bool:
        """Check if the workspace access request requires manager approval."""
        return not self.is_auto_approve

    @property
    def has_access_granted(self) -> bool:
        """Check if access has been granted."""
        from app.state_machines.facts import has_fact
        return has_fact(self.db, self.request.id, "access_granted")

    async def on_enter_manager_approval_async(self):
        """Execute async tasks when entering manager approval state."""
        import logging
        from app.providers.entra_id.client import EntraIdProvider
        from app.core.config import settings
        logger = logging.getLogger(__name__)
        
        ctx = self.request.state_context or {}
        user_email = ctx.get("requested_by_email")
        
        if not user_email:
            logger.warning(f"[{self.request.id}] No requested_by_email found in context. Cannot fetch manager.")
            return
            
        try:
            # Initialize the Entra ID Provider
            provider = EntraIdProvider(
                tenant_id=getattr(settings, "ENTRA_ID_TENANT_ID", "mock-tenant-id"),
                client_id=getattr(settings, "ENTRA_ID_CLIENT_ID", "mock-client-id"),
                client_secret=getattr(settings, "ENTRA_ID_CLIENT_SECRET", "mock-client-secret")
            )
            
            async with provider:
                logger.info(f"[{self.request.id}] Fetching manager for {user_email} from Entra ID")
                # For local dev, we might want to mock this if the real API isn't available
                # manager_email = await provider.get_user_manager(user_email)
                
                # Mocking the manager email for now
                manager_email = f"manager-of-{user_email.split('@')[0]}@example.com"
                
                if manager_email:
                    logger.info(f"[{self.request.id}] Found manager: {manager_email}")
                    ctx["manager_email"] = manager_email
                    self.request.state_context = ctx
                    self.db.commit()
                    
                    # Update the pending approval task with the manager's email
                    from app.db import ApprovalModel
                    pending_approval = self.db.query(ApprovalModel).filter(
                        ApprovalModel.request_id == self.request.id,
                        ApprovalModel.approval_type == "manager",
                        ApprovalModel.status == "pending"
                    ).first()
                    
                    if pending_approval:
                        pending_approval.assigned_to_email = manager_email
                        self.db.commit()
                else:
                    logger.warning(f"[{self.request.id}] No manager found for {user_email} in Entra ID")
                    
        except Exception as e:
            logger.error(f"[{self.request.id}] Failed to fetch manager from Entra ID: {e}")
            # We don't raise here, as we might want to fallback to a default approver or handle it differently
            # For now, the approval will just be unassigned (or assigned to a default if configured)

    async def on_enter_provisioning_async(self):
        """Execute async tasks when entering provisioning state."""
        import logging
        from datetime import datetime
        from app.state_machines.facts import add_fact
        from app.providers.entra_id.client import EntraIdProvider
        from app.core.config import settings
        logger = logging.getLogger(__name__)
        
        ctx = self.request.state_context or {}
        workspace_id = ctx.get("workspace_id")
        workspace_name = ctx.get("workspace_name")
        user_email = ctx.get("requested_by_email")
        
        logger.info(f"[{self.request.id}] Provisioning workspace access for {user_email} to workspace {workspace_id}")
        
        # Determine the target Entra ID group. 
        # TODO: Implement a robust mapping from workspace to Entra ID group.
        # For now, we assume a naming convention or use a placeholder group ID.
        target_group_id = ctx.get("target_group_id", f"group-for-{workspace_name}")
        
        try:
            # Initialize the Entra ID Provider
            provider = EntraIdProvider(
                tenant_id=getattr(settings, "ENTRA_ID_TENANT_ID", "mock-tenant-id"),
                client_id=getattr(settings, "ENTRA_ID_CLIENT_ID", "mock-client-id"),
                client_secret=getattr(settings, "ENTRA_ID_CLIENT_SECRET", "mock-client-secret")
            )
            
            async with provider:
                # In a real implementation, we'd first look up the user's Entra ID object ID by their email
                # user_search = await provider.search_users(user_email)
                # user_object_id = user_search["results"][0]["id"]
                user_object_id = f"user-obj-id-{user_email}"
                
                logger.info(f"[{self.request.id}] Adding user {user_object_id} to Entra ID group {target_group_id}")
                
                # We comment out the actual API call for local dev/testing unless configured
                # await provider.add_to_group(user_id=user_object_id, group_id=target_group_id)
                
                add_fact(self.db, self.request.id, "access_granted", {
                    "workspace_id": workspace_id,
                    "workspace_name": workspace_name,
                    "user_email": user_email,
                    "entra_id_group": target_group_id,
                    "granted_at": datetime.utcnow().isoformat()
                }, actor="system")
                self.db.commit()
                
        except Exception as e:
            logger.error(f"[{self.request.id}] Failed to provision access via Entra ID: {e}")
            # Depending on requirements, we might want to transition to a 'failed' state here
            # or add an error fact. For now, we'll re-raise.
            raise
