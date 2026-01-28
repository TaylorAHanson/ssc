from datetime import datetime
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import add_fact
import logging

logger = logging.getLogger(__name__)

class SimpleEmailStateMachine(BaseRequestStateMachine):
    """
    Atomic Workflow: Sends an email and completes.
    """
    
    # States
    pending = State("Pending", initial=True)
    sending = State("Sending Email")
    completed = State("Completed", final=True)
    
    # Transitions
    start_sending = pending.to(sending)
    submit = start_sending # Alias for base class auto-submission
    finish = sending.to(completed)
    
    # Facts
    STATE_COMPLETION_FACTS = {
        "pending": "request_submitted",
        "sending": "email_sent"
    }
    
    STATE_LOG_FACTS = {
        "pending": ["request_submitted"],
        "sending": ["email_sent", "email_failed"],
        "completed": ["workflow_completed"]
    }
    
    def on_enter_sending(self):
        """Send the email."""
        # We rely on async hook for actual sending since it's an IO operation
        pass

    async def on_enter_sending_async(self):
        """Async hook to send email."""
        try:
            ctx = self.request.state_context or {}
            recipient = ctx.get("email_to")
            subject = ctx.get("email_subject", "Hello World")
            body = ctx.get("email_body", "This is a test email.")
            
            if not recipient:
                raise ValueError("No recipient specified")
                
            await self._send_notification(subject, body, to_email=recipient)
            
            add_fact(self.db, self.request.id, "email_sent", {"recipient": recipient}, actor="system")
            
            # Auto-advance
            self.finish()
            self.save()
            self.db.commit()
            
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            add_fact(self.db, self.request.id, "email_failed", {"error": str(e)}, actor="system")
            # For simplicity, we don't fail the request, just stay in sending or move to failed in a real scenario
            # Here we might want a 'failed' state, but let's keep it simple.


class CampaignStateMachine(BaseRequestStateMachine):
    """
    Compound Workflow: Sends multiple emails via child requests.
    """
    
    # States
    pending = State("Pending", initial=True)
    running = State("Running Campaign")
    completed = State("Completed", final=True)
    
    # Transitions
    start = pending.to(running)
    submit = start # Alias for base class auto-submission
    finish = running.to(completed)
    
    # Facts - Simplified for experiment
    STATE_COMPLETION_FACTS = {
        "pending": "request_submitted",
        "running": "campaign_finished"
    }
    
    def on_enter_running(self):
        """Spawn child requests."""
        # Typically checking if already spawned to ensure idempotency
        if len(self.get_children()) > 0:
            return

        ctx = self.request.state_context or {}
        recipients = ctx.get("recipients", []) # List of emails
        
        for email in recipients:
            payload = {
                "email_to": email,
                "email_subject": f"Campaign: {self.request.title}",
                "email_body": f"Hello {email}, welcome to the campaign!"
            }
            self.spawn_child_request(
                request_type="simple_email",
                payload=payload,
                title=f"Email to {email}"
            )
            
    def tick(self) -> bool:
        """Override tick to check children completion."""
        changed = super().tick()
        
        if self.current_state.id == "running":
            # Check if all children are done
            if self.all_children_completed():
                add_fact(self.db, self.request.id, "campaign_finished", {}, actor="system")
                self.finish()
                return True
                
        return changed or False
