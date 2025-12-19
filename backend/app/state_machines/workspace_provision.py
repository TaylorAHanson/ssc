"""
Workspace Provision state machine.
"""
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine


class WorkspaceProvisionStateMachine(BaseRequestStateMachine):
    
    # States
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    training_pending = State("training_pending")
    provisioning = State("provisioning")
    completed = State("completed")
    rejected = State("rejected")
    
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
