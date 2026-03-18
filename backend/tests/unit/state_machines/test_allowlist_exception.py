import pytest
from datetime import datetime, timedelta
from tests.harness.context import StateMachineTestHarness
from tests.factories.request_factory import RequestFactory
from app.db.allowlist import AllowlistModel
from app.state_machines.factory import get_state_machine
from app.state_machines.facts import add_fact

@pytest.mark.asyncio
async def test_allowlist_exception_lifecycle(db_session):
    harness = StateMachineTestHarness(db_session)
    
    # 1. Create Request
    request = RequestFactory.create(
        db_session, 
        type="allowlist_exception",
        title="Allowlist fin-forecast-app",
        state_context={
            "workspace": "ws-enterprise-prod",
            "resource_type": "app",
            "resource_id": "fin-forecast-app",
            "justification": "Important",
            "expires_at": (datetime.utcnow() + timedelta(days=365)).isoformat()
        }
    )
    
    # Manually transition pending -> recording_pending
    harness.tick(request.id)
    harness.assert_state(request.id, "recording_pending")
    
    sm = get_state_machine(request, db_session)
    
    # Execute async hook to create DB record
    await sm.on_enter_recording_pending_async()
    
    # Verify DB record created as pending
    db_entry = db_session.query(AllowlistModel).filter(AllowlistModel.request_id == request.id).first()
    assert db_entry is not None
    assert db_entry.status == "pending"
    assert db_entry.resource_id == "fin-forecast-app"
    assert db_entry.workspace == "ws-enterprise-prod"
    assert db_entry.justification == "Important"
    
    # Tick to transition recording_pending -> platform_admin_approval
    harness.tick(request.id)
    harness.assert_state(request.id, "platform_admin_approval")
    
    # Simulate platform admin approval task creation (sync hook)
    sm.on_enter_platform_admin_approval()
    harness.assert_fact(request.id, "platform_admin_approval_created")
    
    # Simulate platform admin actual approval from UI
    add_fact(db_session, request.id, "approval_received", {"approval_type": "platform_admin", "approved_by": "admin@qualcomm.com"}, actor="admin")
    db_session.commit()
    
    # Tick to transition to updating_allowlist
    harness.tick(request.id)
    harness.assert_state(request.id, "updating_allowlist")
    
    # Re-fetch state machine to get the updated state
    sm = get_state_machine(request, db_session)
    
    # Execute async hook to update DB record
    await sm.on_enter_updating_allowlist_async()
    
    # Verify DB record is approved
    db_session.refresh(db_entry)
    assert db_entry.status == "approved"
    assert db_entry.approved_by == "admin@qualcomm.com"
    
    # Tick to transition to completed
    harness.tick(request.id)
    harness.assert_state(request.id, "completed")

@pytest.mark.asyncio
async def test_allowlist_exception_rejection(db_session):
    harness = StateMachineTestHarness(db_session)
    
    request = RequestFactory.create(
        db_session, 
        type="allowlist_exception",
        title="Allowlist risky-app",
        state_context={
            "workspace": "ws-enterprise-prod",
            "resource_type": "app",
            "resource_id": "risky-app",
            "justification": "Because I want to"
        }
    )
    
    harness.tick(request.id)
    sm = get_state_machine(request, db_session)
    await sm.on_enter_recording_pending_async()
    
    db_entry = db_session.query(AllowlistModel).filter(AllowlistModel.request_id == request.id).first()
    assert db_entry.status == "pending"
    
    harness.tick(request.id)
    harness.assert_state(request.id, "platform_admin_approval")
    
    # Simulate rejection
    add_fact(db_session, request.id, "request_rejected", {"rejection_note": "Denied"}, actor="admin")
    db_session.commit()
    
    harness.tick(request.id)
    harness.assert_state(request.id, "rejected")
    
    sm = get_state_machine(request, db_session)
    await sm.on_enter_rejected_async()
    
    db_session.refresh(db_entry)
    assert db_entry.status == "rejected"
