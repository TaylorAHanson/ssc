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
    # Using python-statemachine's built-in conditional transitions
    # See: https://python-statemachine.readthedocs.io/en/latest/guards.html#conditions
    
    submit = pending.to(manager_approval, cond="has_request_submitted")
    
    # Manager approval can go to training (if required) or provisioning (if not)
    approve_manager = (
        manager_approval.to(training_pending, cond="has_manager_approval and requires_training") |
        manager_approval.to(provisioning, cond="has_manager_approval and not requires_training")
    )
    
    complete_training = training_pending.to(provisioning, cond="has_training_completed")
    finish_provisioning = provisioning.to(completed, cond="has_workspace_created")
    
    # Rejection can happen from any state
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        manager_approval.to(rejected, cond="has_request_rejected") |
        training_pending.to(rejected, cond="has_request_rejected")
    )
    
    # Approval node configuration
    APPROVAL_NODES = {
        "manager_approval": {"approval_type": "manager", "name": "Manager Approval"}
    }
    
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
