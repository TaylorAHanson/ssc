"""
Data Access state machine.
"""
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine


class DataAccessStateMachine(BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    data_owner_approval = State("data_owner_approval")
    provisioning = State("provisioning")
    completed = State("completed")
    rejected = State("rejected")

    submit = pending.to(data_owner_approval, cond="has_request_submitted")
    approve_owner = data_owner_approval.to(provisioning, cond="has_data_owner_approval")
    finish_provisioning = provisioning.to(completed, cond="has_workspace_created")
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        data_owner_approval.to(rejected, cond="has_request_rejected")
    )
    
    # Approval node configuration
    APPROVAL_NODES = {
        "data_owner_approval": {"approval_type": "data_owner", "name": "Data Owner Approval"}
    }
