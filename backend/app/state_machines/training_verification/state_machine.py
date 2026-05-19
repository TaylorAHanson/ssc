"""
Training Verification state machine.
Verifies a user has completed required training.
"""
from statemachine import State
from app.models.request import RequestType
from app.state_machines.decorators import workflow
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import has_fact, add_fact
from app.core.exceptions import PermanentError, RetryableError
import logging

logger = logging.getLogger(__name__)

@workflow(request_types=RequestType.TRAINING_VERIFICATION, feature_flag="core")
class TrainingVerificationStateMachine(BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    verifying = State("verifying")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    failed = State("failed", final=True)

    submit = pending.to(verifying, cond="has_request_submitted")
    finish_verifying = verifying.to(completed, cond="has_verification_completed")
    
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        verifying.to(rejected, cond="has_request_rejected")
    )
    
    mark_failed = (
        pending.to(failed) |
        verifying.to(failed)
    )

    APPROVAL_NODES = {}
    
    @property
    def has_verification_completed(self) -> bool:
        return has_fact(self.db, self.request.id, "verification_completed")

    async def on_enter_verifying_async(self):
        if has_fact(self.db, self.request.id, "verification_started"):
            return
            
        try:
            add_fact(self.db, self.request.id, "verification_started", {}, actor="system")
            self.db.commit()
            
            ctx = self.request.state_context or {}
            user_email = ctx.get("user_email") or self.request.requester_email
            course_id = ctx.get("course_id")
            
            if not user_email or not course_id:
                raise PermanentError("user_email and course_id are required")
                
            # TODO: Call external learning API/provider to verify completion
            logger.info(f"[{self.request.id}] Verifying training {course_id} for {user_email}")
            
            add_fact(self.db, self.request.id, "verification_completed", {
                "user_email": user_email,
                "course_id": course_id,
                "status": "verified"
            }, actor="system")
            
        except Exception as e:
            logger.error(f"[{self.request.id}] Training verification failed: {e}")
            raise RetryableError(f"Failed to verify training: {e}")
