"""
Data Access state machine.
"""
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine


class DataAccessStateMachine(BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    data_owner_approval = State("data_owner_approval")
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    failed = State("failed", final=True)

    submit = pending.to(data_owner_approval, cond="has_request_submitted")
    
    # Handle legacy manager approval state if it exists
    submit_to_manager = pending.to(manager_approval, cond="has_request_submitted")
    approve_manager = manager_approval.to(data_owner_approval, cond="has_manager_approval")
    
    approve_owner = data_owner_approval.to(provisioning, cond="has_data_owner_approval")
    finish_provisioning = provisioning.to(completed, cond="has_workspace_created")
    
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        manager_approval.to(rejected, cond="has_request_rejected") |
        data_owner_approval.to(rejected, cond="has_request_rejected")
    )
    
    mark_failed = (
        pending.to(failed) |
        manager_approval.to(failed) |
        data_owner_approval.to(failed) |
        provisioning.to(failed)
    )
    
    # Approval node configuration
    APPROVAL_NODES = {
        "manager_approval": {"approval_type": "manager", "name": "Manager Approval"},
        "data_owner_approval": {"approval_type": "data_owner", "name": "Data Owner Approval"}
    }

    async def on_enter_provisioning_async(self):
        """Execute async tasks for provisioning state."""
        # Notify user: Approved
        await self._send_notification(
            subject=f"Data Access Request Approved: {self.request.title}",
            body=f"Your data access request '{self.request.title}' has been approved by the data owner. Access is being provisioned."
        )
        
    async def on_enter_completed_async(self):
        """Execute async tasks for completed state."""
        # Notify user: Success
        await self._send_notification(
            subject=f"Data Access Granted: {self.request.title}",
            body=f"Your data access request '{self.request.title}' has been successfully completed. You should now have access."
        )
