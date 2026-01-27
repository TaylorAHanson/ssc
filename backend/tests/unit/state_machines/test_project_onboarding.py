from datetime import datetime
from tests.harness.context import StateMachineTestHarness
from tests.factories.request_factory import RequestFactory

def test_project_onboarding_lifecycle(db_session):
    harness = StateMachineTestHarness(db_session)
    
    # 1. Create Request
    request = RequestFactory.create(
        db_session, 
        type="project_onboarding",
        title="Onboard New Alpha",
        state_context={
            "project_name": "alpha",
            "cost_center": "1234"
        }
    )
    
    # 2. Initial State -> Manager Approval
    # The 'pending' state checks for 'request_submitted' fact, which is added automatically by the base class tick()
    # or we simulate submission.
    harness.tick(request.id)
    harness.assert_state(request.id, "manager_approval")
    
    # 3. Manager Approval -> Provisioning
    # We need to inject an approval fact
    from app.state_machines.facts import add_fact
    add_fact(db_session, request.id, "approval_received", {"approval_type": "manager"}, actor="manager")
    db_session.commit()
    
    harness.tick(request.id)
    harness.assert_state(request.id, "provisioning")
    
    # 4. Verify Child Requests Spawned
    # The 'on_enter_provisioning_async' should have run spawned children
    # NOTE: In sync tests, async hooks need to be run manually or via a helper if we want to test side effects
    # But spawn_child_request is synchronous in our implementation? Let's check project_onboarding.py
    
    # Correct, spawn_child_request is sync. 
    # But wait, on_enter_provisioning_async is async. 
    # The base.tick() calls _call_on_enter_hooks (sync) and returns.
    # It does NOT verify async hooks. 
    # To test async hooks, we should manually invoke them or mock the async execution
    
    # For now, let's verify if children are created. 
    # Since we replaced execute_tasks (async) with on_enter_provisioning_async (async)
    # The runner (Poller) usually runs the async tasks. 
    # harness.tick() currently doesn't run async tasks.
    
    # Let's verify the logic by manually running the async hook for testing purposes
    from app.state_machines.factory import get_state_machine
    import asyncio
    
    sm = get_state_machine(request, db_session)
    asyncio.run(sm.on_enter_provisioning_async())
    
    children = sm.get_children()
    assert len(children) == 2
    types = [c.type for c in children]
    assert "workspace_provision" in types
    assert "github_repo_creation" in types
