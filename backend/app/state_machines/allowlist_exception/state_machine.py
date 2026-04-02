import logging
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import add_fact
from app.db.allowlist import AllowlistModel
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)

class AllowlistExceptionStateMachine(BaseRequestStateMachine):
    """
    State machine for Allowlist Exception requests.
    - Immediately creates a 'pending' entry in the DB.
    - Waits for Platform Admin approval.
    - Updates entry to 'approved' or 'rejected'.
    """

    pending = State("Pending", initial=True)
    recording_pending = State("Recording Pending State")
    platform_admin_approval = State("Platform Admin Approval")
    updating_allowlist = State("Updating Allowlist")
    completed = State("Completed", final=True)
    rejected = State("Rejected", final=True)
    failed = State("Failed", final=True)

    # Transitions
    submit = pending.to(recording_pending, cond="has_request_submitted")
    
    # After recording pending, move to approval
    wait_for_approval = recording_pending.to(platform_admin_approval, cond="has_pending_recorded")
    
    # From approval
    approve = platform_admin_approval.to(updating_allowlist, cond="has_platform_admin_approval")
    reject = platform_admin_approval.to(rejected, cond="has_request_rejected")
    
    # After updating
    finish = updating_allowlist.to(completed, cond="has_allowlist_updated")
    
    # Error handling
    fail = (
        pending.to(failed, cond="has_error") |
        recording_pending.to(failed, cond="has_error") |
        platform_admin_approval.to(failed, cond="has_error") |
        updating_allowlist.to(failed, cond="has_error")
    )

    @property
    def has_pending_recorded(self) -> bool:
        return self.has_fact("pending_recorded")

    @property
    def has_allowlist_updated(self) -> bool:
        return self.has_fact("allowlist_updated")

    @property
    def has_rejection(self) -> bool:
        return self.has_fact("rejection")

    @property
    def has_error(self) -> bool:
        return self.has_fact("error_occurred")

    def has_fact(self, fact_type: str) -> bool:
        from app.state_machines.facts import has_fact as check_fact
        return check_fact(self.db, self.request.id, fact_type)

    async def on_enter_recording_pending_async(self):
        """Immediately create the allowlist entry in 'pending' status so the Sentinel grants a reprieve."""
        if self.has_fact("pending_recorded"):
            return
            
        params = self.request.state_context
        
        expires_at = None
        if params.get("expires_at"):
            try:
                expires_at = datetime.fromisoformat(params["expires_at"].replace('Z', '+00:00'))
            except:
                pass
                
        db_obj = AllowlistModel(
            id=str(uuid.uuid4()),
            resource_id=params["resource_id"],
            resource_type=params["resource_type"],
            workspace=params["workspace"],
            justification=params["justification"],
            status="pending",
            request_id=self.request.id,
            expires_at=expires_at
        )
        self.db.add(db_obj)
        self.db.commit()
        
        add_fact(self.db, self.request.id, "pending_recorded", {"allowlist_id": db_obj.id})
        self.wait_for_approval()

    def on_enter_platform_admin_approval(self):
        """Create the approval task for platform admins."""
        if self.has_fact("platform_admin_approval_created"):
            return
            
        self.create_approval_task("platform_admin")
        
        add_fact(self.db, self.request.id, "platform_admin_approval_created", {})

    async def on_enter_updating_allowlist_async(self):
        """Update the existing pending record to approved."""
        if self.has_fact("allowlist_updated"):
            return
            
        entry = self.db.query(AllowlistModel).filter(AllowlistModel.request_id == self.request.id).first()
        if entry:
            entry.status = "approved"
            
            # Record who approved it based on the approval fact
            approval_fact = next((f for f in self.request.events if f.event_type == "approval_received" and f.event_data.get("approval_type") == "platform_admin"), None)
            if approval_fact and approval_fact.event_data:
                entry.approved_by = approval_fact.event_data.get("approved_by")
                
            self.db.commit()
            
        add_fact(self.db, self.request.id, "allowlist_updated", {})
        self.finish()
        
    async def on_enter_rejected_async(self):
        """Update the pending record to rejected if it exists."""
        entry = self.db.query(AllowlistModel).filter(AllowlistModel.request_id == self.request.id).first()
        if entry and entry.status == "pending":
            entry.status = "rejected"
            self.db.commit()
