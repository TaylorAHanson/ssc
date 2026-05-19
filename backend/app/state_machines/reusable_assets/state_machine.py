"""
Reusable Assets informational state machine.
This is mostly an agent-driven flow, but we provide a state machine to track the request.
"""
from statemachine import State
from app.models.request import RequestType
from app.state_machines.decorators import workflow
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import has_fact, add_fact
import logging

logger = logging.getLogger(__name__)

@workflow(request_types=RequestType.REUSABLE_ASSETS, feature_flag="core")
class ReusableAssetsStateMachine(BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    completed = State("completed", final=True)

    submit = pending.to(completed, cond="has_request_submitted")
    
    APPROVAL_NODES = {}
    
    async def on_enter_completed_async(self):
        # Just record that we fulfilled an informational request
        pass
