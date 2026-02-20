import pytest
import asyncio
from typing import List
from sqlalchemy.orm import Session
from app.models.request import RequestType
from tests.factories.request_factory import RequestFactory
from tests.harness.context import StateMachineTestHarness
from app.state_machines.factory import get_state_machine
from app.db.request import RequestModel

# Async test marker
pytestmark = pytest.mark.asyncio

async def manual_tick_children(db: Session, parent_id: str):
    """Helper to tick all children of a request until they complete."""
    # This simulates the worker picking up child requests
    parent = db.query(RequestModel).filter(RequestModel.id == parent_id).first()
    children = db.query(RequestModel).filter(RequestModel.parent_id == parent_id).all()
    
    for child in children:
        print(f"DEBUG: Processing child {child.id} ({child.type})")
        sm = get_state_machine(child, db)
        
        # 1. Pending -> Sending
        if sm.current_state.id == "pending":
            sm.tick()
            sm.save()
            db.commit()
            
        # 2. Sending -> Completed (Async Hook)
        if sm.current_state.id == "sending":
            # Manually run the async hook because we are not running the real poller
            await sm.on_enter_sending_async() 
            # The async hook should have called finish() and save()
            
            # Reload to check status
            db.refresh(child)
            print(f"DEBUG: Child {child.id} status: {child.status}")


async def test_campaign_workflow_experiment(db_session: Session):
    """
    Verifies the Campaign -> SimpleEmail compound workflow.
    """
    harness = StateMachineTestHarness(db_session)
    
    # 1. Create Campaign Request
    recipients = ["user1@example.com", "user2@example.com"]
    campaign = RequestFactory.create(
        db_session,
        type=RequestType.CAMPAIGN,
        title="Test Campaign",
        state_context={
            "recipients": recipients,
            "requested_by_email": "admin@example.com"
        }
    )
    
    print(f"DEBUG: Created Campaign {campaign.id}")
    
    # 2. Start Campaign (Pending -> Training Pending)
    # The tick will transition to Training Pending
    harness.tick(campaign.id)
    harness.assert_state(campaign.id, "training_pending")

    # 2.5 Add training completed fact (Training Pending -> Running)
    from app.state_machines.facts import add_fact
    add_fact(db_session, campaign.id, "training_completed", {}, actor="system")

    # Tick again to move to Running and spawn children
    harness.tick(campaign.id)
    harness.assert_state(campaign.id, "running")
    
    # Verify children spawned
    children = db_session.query(RequestModel).filter(RequestModel.parent_id == campaign.id).all()
    assert len(children) == 2
    assert {c.state_context["email_to"] for c in children} == set(recipients)
    
    # 3. Process Children
    # The campaign should stay 'running' until children are done
    harness.tick(campaign.id) 
    harness.assert_state(campaign.id, "running")
    
    # Simulate worker processing children
    await manual_tick_children(db_session, campaign.id)
    
    # 4. Finish Campaign
    # Now that children are completed, the next tick on campaign should finish it
    harness.tick(campaign.id)
    harness.assert_state(campaign.id, "completed")
    
    print("DEBUG: Campaign completed successfully")
