import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from tests.harness.context import StateMachineTestHarness
from tests.factories.request_factory import RequestFactory
from app.state_machines.github_repo.state_machine import GithubRepoCreationStateMachine
from app.state_machines.facts import has_fact

def test_github_repo_provisioning_flow(db_session):
    harness = StateMachineTestHarness(db_session)
    
    # 1. Create Request
    request = RequestFactory.create(
        db_session, 
        type="github_repo_creation",
        title="Create New Repo",
        state_context={
            "repo_name": "test-repo",
            "description": "Test Repository",
            "visibility": "private",
            "template": "none"
        }
    )
    
    # 2. Initial State -> Provisioning
    # Manually transition to provisioning by simulating submission
    # The 'pending' state checks for 'request_submitted' fact
    # BaseRequestStateMachine should add this if we use the harness properly or we can add it manually
    
    # Let's just tick it once to get to provisioning if it's pending
    # But wait, submit transition requires "has_request_submitted"
    # RequestFactory creates a request in 'pending' state usually.
    # In BaseRequestStateMachine, if it's new, it might not have facts.
    
    # Let's add the fact to move it forward
    from app.state_machines.facts import add_fact
    add_fact(db_session, request.id, "request_submitted", {}, actor="user")
    db_session.commit()
    
    harness.tick(request.id)
    harness.assert_state(request.id, "provisioning")
    
    # 3. Test on_enter_provisioning_async
    # We need to mock the GitHubProvider
    
    with patch("app.providers.github.client.GitHubProvider") as MockProvider, \
         patch("app.core.config.settings") as mock_settings:
        
        # Setup Mock
        mock_github = AsyncMock()
        MockProvider.return_value.__aenter__.return_value = mock_github
        
        mock_settings.GITHUB_TOKEN = "fake-token"
        mock_settings.GITHUB_ORG = "fake-org"
        
        mock_github.create_repo.return_value = {
            "html_url": "https://github.com/fake-org/test-repo",
            "full_name": "fake-org/test-repo"
        }
        
        # Manually run the async hook
        import asyncio
        sm = GithubRepoCreationStateMachine(request, db_session)
        asyncio.run(sm.on_enter_provisioning_async())
        
        # Verify Provider called
        mock_github.create_repo.assert_awaited_with(
            "test-repo", 
            {"description": "Test Repository", "private": True}
        )
        
        # Verify Facts
        assert has_fact(db_session, request.id, "provisioning_started")
        assert has_fact(db_session, request.id, "repo_created")
        assert has_fact(db_session, request.id, "provisioning_completed")
        
    # 4. Tick again to complete
    harness.tick(request.id)
    harness.assert_state(request.id, "completed")

def test_github_repo_from_template(db_session):
    harness = StateMachineTestHarness(db_session)
    
    request = RequestFactory.create(
        db_session, 
        type="github_repo_creation",
        title="Create Repo From Template",
        state_context={
            "repo_name": "templated-repo",
            "template": "my-template"
        }
    )
    
    from app.state_machines.facts import add_fact
    add_fact(db_session, request.id, "request_submitted", {}, actor="user")
    db_session.commit()
    
    harness.tick(request.id)
    harness.assert_state(request.id, "provisioning")
    
    with patch("app.providers.github.client.GitHubProvider") as MockProvider, \
         patch("app.core.config.settings") as mock_settings:
        
        mock_github = AsyncMock()
        MockProvider.return_value.__aenter__.return_value = mock_github
        
        mock_github.create_from_template.return_value = {
            "html_url": "https://github.com/fake-org/templated-repo",
            "full_name": "fake-org/templated-repo"
        }
        
        import asyncio
        sm = GithubRepoCreationStateMachine(request, db_session)
        asyncio.run(sm.on_enter_provisioning_async())
        
        mock_github.create_from_template.assert_awaited_once()
        args = mock_github.create_from_template.call_args
        assert args[0][0] == "my-template"
        assert args[0][1] == "templated-repo"
        
        assert has_fact(db_session, request.id, "repo_created")

