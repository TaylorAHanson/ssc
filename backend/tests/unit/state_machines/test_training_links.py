from tests.harness.context import StateMachineTestHarness
from tests.factories.request_factory import RequestFactory
import asyncio
from app.state_machines.factory import get_state_machine

def test_training_links_lifecycle(db_session):
    harness = StateMachineTestHarness(db_session)
    
    request = RequestFactory.create(
        db_session, 
        type="training_links",
        state_context={
            "topic": "Spark",
            "skill_level": "Beginner"
        }
    )
    
    harness.tick(request.id)
    harness.assert_state(request.id, "completed")
