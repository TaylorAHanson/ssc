from tests.harness.context import StateMachineTestHarness
from tests.factories.request_factory import RequestFactory
from app.state_machines.facts import add_fact
import asyncio
from app.state_machines.factory import get_state_machine
from unittest.mock import patch, MagicMock, AsyncMock

def test_github_repo_access_lifecycle(db_session):
    harness = StateMachineTestHarness(db_session)
    
    request = RequestFactory.create(
        db_session, 
        type="github_repo_access",
        state_context={
            "repo_name": "my-repo",
            "github_username": "octocat",
            "permission": "push"
        }
    )
    
    harness.tick(request.id)
    harness.assert_state(request.id, "manager_approval")
    
    add_fact(db_session, request.id, "approval_received", {"approval_type": "manager"}, actor="manager")
    db_session.commit()
    
    harness.tick(request.id)
    harness.assert_state(request.id, "data_owner_approval")
    
    from unittest.mock import patch, AsyncMock
    with patch("app.state_machines.github_repo_access.state_machine.settings.PLATFORM_ADMIN_EMAIL", "admin@example.com"):
        sm = get_state_machine(request, db_session)
        asyncio.run(sm.on_enter_data_owner_approval_async())
    
    add_fact(db_session, request.id, "approval_received", {"approval_type": "data_owner"}, actor="owner")
    db_session.commit()
    
    harness.tick(request.id)
    harness.assert_state(request.id, "provisioning")
    
    # Mock GitHub provider
    with patch("app.providers.github.client.GitHubProvider") as MockProvider:
        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value = mock_instance
        MockProvider.return_value = mock_instance
    
        asyncio.run(sm.on_enter_provisioning_async())
        
        mock_instance.set_permissions.assert_called_once_with("my-repo", "octocat", "push")
    
    harness.tick(request.id)
    harness.assert_state(request.id, "completed")
