from tests.harness.context import StateMachineTestHarness
from tests.factories.request_factory import RequestFactory
from app.state_machines.facts import add_fact
import asyncio
from app.state_machines.factory import get_state_machine
from unittest.mock import patch, MagicMock

def test_volume_creation_lifecycle(db_session):
    harness = StateMachineTestHarness(db_session)
    
    request = RequestFactory.create(
        db_session, 
        type="volume_creation",
        state_context={
            "name": "my-volume",
            "catalog": "my-catalog",
            "schema": "my-schema"
        }
    )
    
    harness.tick(request.id)
    harness.assert_state(request.id, "manager_approval")
    
    add_fact(db_session, request.id, "approval_received", {"approval_type": "manager"}, actor="manager")
    db_session.commit()
    
    harness.tick(request.id)
    harness.assert_state(request.id, "terraform_planning")
    
    # Mock GitOps provider
    with patch("app.state_machines.volume_creation.state_machine.VolumeCreationStateMachine._get_provider") as MockProvider:
        from unittest.mock import AsyncMock
        mock_provider = AsyncMock()
        MockProvider.return_value = mock_provider
        
        sm = get_state_machine(request, db_session)
        asyncio.run(sm.on_enter_terraform_planning_async())
        
        mock_provider.plan.assert_called_once()
    
    # Simulate receiving the plan callback
    add_fact(db_session, request.id, "terraform_plan_received", {"status": "success"}, actor="system")
    db_session.commit()
    
    harness.tick(request.id)
    harness.assert_state(request.id, "awaiting_approval")
    
    add_fact(db_session, request.id, "approval_received", {"approval_type": "platform_admin"}, actor="admin")
    db_session.commit()
    
    harness.tick(request.id)
    harness.assert_state(request.id, "terraform_applying")
    
    with patch("app.state_machines.volume_creation.state_machine.VolumeCreationStateMachine._get_provider") as MockProvider:
        from unittest.mock import AsyncMock
        mock_provider = AsyncMock()
        MockProvider.return_value = mock_provider
        
        asyncio.run(sm.on_enter_terraform_applying_async())
        
        mock_provider.apply.assert_called_once()
        
    # Simulate receiving the apply callback
    add_fact(db_session, request.id, "terraform_apply_received", {"status": "success"}, actor="system")
    db_session.commit()
    
    harness.tick(request.id)
    harness.assert_state(request.id, "completed")
