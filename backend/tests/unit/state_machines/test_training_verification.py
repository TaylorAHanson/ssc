from tests.harness.context import StateMachineTestHarness
from tests.factories.request_factory import RequestFactory
from app.state_machines.facts import add_fact
import asyncio
from app.state_machines.factory import get_state_machine

def test_training_verification_lifecycle(db_session):
    harness = StateMachineTestHarness(db_session)
    
    request = RequestFactory.create(
        db_session, 
        type="training_verification",
        state_context={
            "user_email": "user@example.com",
            "course_id": "course-123"
        }
    )
    
    harness.tick(request.id)
    harness.assert_state(request.id, "verifying")
    
    from unittest.mock import patch
    with patch("app.providers.training.client.TrainingProvider.__init__", return_value=None), \
         patch("app.providers.training.client.TrainingProvider.get_user_training_status", return_value=["course-123"]):
        sm = get_state_machine(request, db_session)
        asyncio.run(sm.on_enter_verifying_async())
    
    harness.tick(request.id)
    harness.assert_state(request.id, "completed")
