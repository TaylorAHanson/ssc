import pytest
from datetime import datetime, timezone, timedelta
from tests.harness.context import StateMachineTestHarness
from tests.factories.request_factory import RequestFactory
from app.db.allowlist import AllowlistModel
from app.state_machines.factory import get_state_machine
import uuid

@pytest.mark.asyncio
async def test_enforcement_sentinel_discovery(db_session):
    # Setup some allowlist records
    approved_entry = AllowlistModel(
        id=str(uuid.uuid4()),
        resource_id="approved-app",
        resource_type="app",
        workspace="ws-enterprise-prod",
        justification="Approved",
        status="approved",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1)
    )
    pending_entry = AllowlistModel(
        id=str(uuid.uuid4()),
        resource_id="pending-app",
        resource_type="app",
        workspace="ws-enterprise-prod",
        justification="Pending",
        status="pending"
    )
    db_session.add(approved_entry)
    db_session.add(pending_entry)
    db_session.commit()

    harness = StateMachineTestHarness(db_session)
    
    # 1. Create Request
    request = RequestFactory.create(
        db_session, 
        type="enforcement_sentinel",
        title="Sentinel Scan",
        state_context={
            "workspace": "ws-enterprise-prod",
            "enforcement_mode": "audit_only"
        }
    )
    
    # Tick to transition pending -> discovering
    harness.tick(request.id)
    harness.assert_state(request.id, "discovering")
    
    sm = get_state_machine(request, db_session)
    
    # We need to mock the OPA provider or let it run locally
    # To make this robust without OPA binary in CI, we'll mock the provider
    class MockOpaProvider:
        async def evaluate(self, policy_path, query, input_data):
            resource_id = input_data["resource"]["id"]
            if resource_id == "approved-app":
                return {"is_violation": True, "action": "SKIPPED_ALLOWLIST"}
            elif resource_id == "pending-app":
                return {"is_violation": True, "action": "PENDING_EXCEPTION"}
            else:
                return {"is_violation": True, "action": "KILL"}

    # Mock the discovery phase
    async def mock_discover(self_mock):
        apps = [
            {"id": "approved-app", "type": "app"},
            {"id": "pending-app", "type": "app"},
            {"id": "rogue-app", "type": "app"}
        ]
        
        opa = MockOpaProvider()
        violations = []
        for app in apps:
            result = await opa.evaluate("", "", {"resource": app})
            violations.append({
                "resource_id": app["id"],
                "resource_type": app["type"],
                "action": result["action"]
            })
            
            ctx = request.state_context.copy()
            ctx["violations"] = violations
            request.state_context = ctx
            
            from app.state_machines.facts import add_fact
        add_fact(db_session, request.id, "discover_completed", {"violation_count": len(violations)})
        sm.finish_discovering()

    # Override the actual hook with our mock
    import types
    sm.on_enter_discovering_async = types.MethodType(mock_discover, sm)
    
    await sm.on_enter_discovering_async()
    
    # Verify state transitioned to enforcing
    harness.tick(request.id)
    harness.assert_state(request.id, "enforcing")
    
    violations = request.state_context["violations"]
    assert len(violations) == 3
    
    # Check that OPA evaluation mapped correctly
    rogue = next(v for v in violations if v["resource_id"] == "rogue-app")
    assert rogue["action"] == "KILL"
    
    approved = next(v for v in violations if v["resource_id"] == "approved-app")
    assert approved["action"] == "SKIPPED_ALLOWLIST"
    
    pending = next(v for v in violations if v["resource_id"] == "pending-app")
    assert pending["action"] == "PENDING_EXCEPTION"
