from tests.harness.context import StateMachineTestHarness
from tests.factories.request_factory import RequestFactory
from app.state_machines.facts import add_fact
import asyncio
from app.state_machines.factory import get_state_machine

def test_tag_creation_lifecycle(db_session):
    harness = StateMachineTestHarness(db_session)
    
    request = RequestFactory.create(
        db_session, 
        type="tag_creation",
        state_context={
            "tag_name": "CostCenter",
            "tag_value": "12345"
        }
    )
    
    harness.tick(request.id)
    harness.assert_state(request.id, "manager_approval")
    
    add_fact(db_session, request.id, "approval_received", {"approval_type": "manager"}, actor="manager")
    db_session.commit()
    
    harness.tick(request.id)
    harness.assert_state(request.id, "provisioning")
    
    from unittest.mock import patch
    with patch("app.providers.databricks.client.DatabricksProvider.__init__", return_value=None):
        sm = get_state_machine(request, db_session)
        asyncio.run(sm.on_enter_provisioning_async())
    
    harness.tick(request.id)
    harness.assert_state(request.id, "completed")
