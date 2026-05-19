from datetime import datetime
from tests.harness.context import StateMachineTestHarness
from tests.factories.request_factory import RequestFactory
import pytest

def test_project_onboarding_lifecycle(db_session):
    harness = StateMachineTestHarness(db_session)
    
    # 1. Create Request
    request = RequestFactory.create(
        db_session, 
        type="project_onboarding",
        title="Onboard New Alpha",
        state_context={
            "project_name": "alpha",
            "cost_center": "1234",
            "team_members": ["user1@example.com", "user2@example.com"],
            "datasets": [
                {"name": "main.default.table1", "type": "table", "access_level": "read"}
            ]
        }
    )
    
    # 2. Initial State -> Manager Approval
    harness.tick(request.id)
    harness.assert_state(request.id, "manager_approval")
    
    # 3. Manager Approval -> Provisioning
    from app.state_machines.facts import add_fact
    add_fact(db_session, request.id, "approval_received", {"approval_type": "manager"}, actor="manager")
    db_session.commit()
    
    harness.tick(request.id)
    harness.assert_state(request.id, "provisioning")
    
    # 4. Verify Child Requests Spawned
    from app.state_machines.factory import get_state_machine
    import asyncio
    
    sm = get_state_machine(request, db_session)
    asyncio.run(sm.on_enter_provisioning_async())
    
    children = sm.get_children()
    # 1 workspace + 1 repo + 2 workspace access + 1 data access = 5 children
    assert len(children) == 5
    types = [c.type for c in children]
    assert "workspace_provision" in types
    assert "github_repo_creation" in types
    assert "workspace_access" in types
    assert "catalog_schema_table_access" in types
    
    # Check that children_spawned fact was added
    from app.state_machines.facts import has_fact
    assert has_fact(db_session, request.id, "children_spawned")
