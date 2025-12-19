"""
GitHub Repository Creation state machine.
"""
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import has_fact, add_fact
import logging

logger = logging.getLogger(__name__)


class GithubRepoCreationStateMachine(BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)

    submit = pending.to(manager_approval, cond="has_request_submitted")
    approve_manager = manager_approval.to(provisioning, cond="has_manager_approval")
    finish_provisioning = provisioning.to(completed, cond="has_repo_created")
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        manager_approval.to(rejected, cond="has_request_rejected")
    )
    
    # Approval node configuration
    APPROVAL_NODES = {
        "manager_approval": {"approval_type": "manager", "name": "Manager Approval"}
    }
    
    @property
    def has_repo_created(self) -> bool:
        """Check if GitHub repository has been created."""
        return has_fact(self.db, self.request.id, "repo_created")
    
    def _process_current_state(self) -> bool:
        """
        Override to handle repo creation facts instead of workspace creation.
        """
        changed = super()._process_current_state()
        
        # Handle provisioning state - check if repo already exists
        if self.current_state.id == "provisioning":
            if has_fact(self.db, self.request.id, "repo_created"):
                # Repo already exists, mark provisioning as completed
                if not has_fact(self.db, self.request.id, "provisioning_completed"):
                    logger.info(f"Repository already exists for request {self.request.id}, marking complete")
                    add_fact(self.db, self.request.id, "provisioning_completed", {}, actor="system")
                    # Will reconcile to completed on next tick
        
        return changed
