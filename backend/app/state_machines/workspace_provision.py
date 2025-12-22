"""
Workspace Provision state machine.
"""
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import has_fact, add_fact
import logging

logger = logging.getLogger(__name__)


class WorkspaceProvisionStateMachine(BaseRequestStateMachine):
    
    # States
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    training_pending = State("training_pending")
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    
    # Transitions with conditional guards based on facts
    submit = pending.to(manager_approval, cond="has_request_submitted")
    
    # Manager approval can go to training (if required) or provisioning (if not)
    # For demo: we allow auto-transition if approval fact exists
    approve_manager = (
        manager_approval.to(training_pending, cond="has_manager_approval and requires_training") |
        manager_approval.to(provisioning, cond="has_manager_approval and not requires_training")
    )
    
    # For demo: auto-approve manager if fact missing but we want to skip
    # (Commented out to keep the flow manual as requested by the bypass button mention)
    # _auto_manager = manager_approval.to(provisioning, cond="not requires_training")
    
    complete_training = training_pending.to(provisioning, cond="has_training_completed")
    finish_provisioning = provisioning.to(completed, cond="has_workspace_created")
    
    # Rejection can happen from any state
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        manager_approval.to(rejected, cond="has_request_rejected") |
        training_pending.to(rejected, cond="has_request_rejected") |
        provisioning.to(rejected, cond="has_request_rejected")
    )
    
    # Approval node configuration
    APPROVAL_NODES = {
        "manager_approval": {"approval_type": "manager", "name": "Manager Approval"}
    }
    
    async def execute_tasks(self):
        """Execute workspace provisioning tasks."""
        if self.current_state.id == "provisioning":
            if not has_fact(self.db, self.request.id, "workspace_created"):
                from app.tools.workspace import CreateWorkspaceTool
                from datetime import datetime
                from app.core.exceptions import ValidationError
                from app.core.config import settings
                
                # Mark provisioning as started if not already marked
                if not has_fact(self.db, self.request.id, "provisioning_started"):
                    add_fact(self.db, self.request.id, "provisioning_started", {"started_at": datetime.utcnow().isoformat()}, actor="system")
                    self.db.commit()
                
                # Extract configuration from request
                state_context = self.request.state_context or {}
                
                # Get workspace name and environment
                workspace_name = state_context.get("workspace_name")
                if not workspace_name:
                    if ":" in self.request.title:
                        workspace_name = self.request.title.split(":")[-1].strip()
                    else:
                        workspace_name = self.request.title
                
                environment = self.request.environment or "dev"
                
                config = {
                    "databricks_account_id": state_context.get("databricks_account_id") or settings.DATABRICKS_ACCOUNT_ID,
                    "client_id": state_context.get("client_id") or settings.DATABRICKS_CLIENT_ID,
                    "client_secret": state_context.get("client_secret") or settings.DATABRICKS_CLIENT_SECRET,
                    "region": state_context.get("region", "eu-west-1"),
                    "cidr_block": state_context.get("cidr_block", "10.4.0.0/16"),
                    "tags": state_context.get("tags", {}),
                    **state_context
                }
                
                # Validate required config
                if not config.get("databricks_account_id"):
                    raise ValidationError("databricks_account_id is required.")
                if not config.get("client_id"):
                    raise ValidationError("client_id is required.")
                if not config.get("client_secret"):
                    raise ValidationError("client_secret is required.")
                
                requested_by = state_context.get("requested_by") or "system"
                
                logger.info(f"[{self.request.id}] Executing CreateWorkspaceTool...")
                tool = CreateWorkspaceTool()
                
                await tool.execute(
                    request_id=self.request.id,
                    name=workspace_name,
                    environment=environment,
                    config=config,
                    requested_by=requested_by,
                    db=self.db
                )

    def _process_current_state(self) -> bool:
        """
        Override to handle provisioning state - mark that provisioning should start.
        
        The actual async tool execution will be handled by the poller.
        We just mark that provisioning should start by checking facts.
        """
        changed = super()._process_current_state()
        
        # Handle provisioning state - check if we need to start provisioning
        if self.current_state.id == "provisioning":
            # Check if provisioning has already started or completed
            if has_fact(self.db, self.request.id, "workspace_created"):
                # Workspace already created - provisioning is done
                if not has_fact(self.db, self.request.id, "provisioning_completed"):
                    logger.info(f"Workspace already exists for request {self.request.id}, marking complete")
                    add_fact(self.db, self.request.id, "provisioning_completed", {}, actor="system")
            elif not has_fact(self.db, self.request.id, "provisioning_started"):
                # Provisioning hasn't started yet - mark that it should start
                # The actual async execution will be handled separately
                logger.info(f"Provisioning state entered for request {self.request.id} - will be processed by poller")
                # Don't start provisioning here - it will be handled by async processing
        
        return changed
